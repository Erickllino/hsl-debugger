"""Sessão SSH do T1 Debug.

O SSH não serve só para autenticar — ele é o transporte dos dados (§1). Este
módulo abre a sessão, sobe o agente e transforma o `stdout` remoto em `Signal`
do Qt. Nenhum widget é tocado aqui.

Decisões implementadas, ambas registradas em `docs/T1_DEBUG.md`:

- transporte é o `ssh` do sistema via `QProcess`, não uma biblioteca SSH em
  Python — o §7 fixa "PySide6 e biblioteca padrão", e de quebra herdamos
  `~/.ssh/config` e `known_hosts` do usuário;
- o `agent.py` vai embutido em base64 na própria linha de comando (§5), numa
  invocação só. Nada é escrito no disco do robô, o que é a diferença que
  importa em robô emprestado.
"""

import base64
import json
import shutil
import stat
import tempfile
from pathlib import Path

from PySide6 import QtCore

PROTOCOL_VERSION = 1
DEFAULT_PORT = 22

# O workspace do time. Vem pré-preenchido no campo *Setup ROS* porque é o caminho
# certo na esmagadora maioria das conexões — e um campo que já vem certo é a
# diferença entre "funciona" e "descobrir por que o tópico está laranja". Não é
# imposição: histórico salvo vence, e apagar a linha volta ao automático.
SETUP_PADRAO = "/hsl-player/install/setup.bash"
CONNECT_TIMEOUT = 15
HELLO_TIMEOUT = 45_000  # ms; inclui achar o ambiente ROS e subir o rclpy.

# O agente se mata depois de 600 s sem receber comando. Olhar um tópico por meia
# hora sem clicar em nada é uso normal, então a sessão precisa dizer que está
# viva. É o `ping` do §4, que existia sem cliente até agora.
KEEPALIVE = 60_000  # ms

# Roda no robô antes do agente. Estratégia em camadas do §5: fontes explícitas
# vencem, depois /opt/ros, depois overlays de workspace, e falhar dizendo o que
# foi procurado. Sem `set -u`: os setup.bash do ROS não sobrevivem a ele.
#
# As fontes explícitas são uma *lista*, uma por linha, e são carregadas na ordem
# digitada — underlay primeiro, overlay depois, que é a ordem que o ament
# respeita. Um caminho só não bastava: as mensagens do SDK moram num workspace
# que não é o mesmo prefixo da distro, e as duas precisam entrar na mesma sessão.
BOOTSTRAP = r"""
setups='@@SETUPS@@'
agent_b64='@@AGENT@@'

tentados=''
carregados=''

carregar() {
    # O `~` não é expandido dentro de variável, então quem expande somos nós.
    # Sem isto, `~/booster_ws/...` vira um caminho literal que não existe.
    case "$1" in
        '~') alvo="$HOME" ;;
        '~/'*) alvo="$HOME/${1#\~/}" ;;
        *) alvo="$1" ;;
    esac

    tentados="$tentados\n    $alvo"
    [ -r "$alvo" ] || return 1
    . "$alvo" >/dev/null 2>&1 || return 1
    carregados="$carregados$alvo "
    echo "carregado: $alvo" >&2
    return 0
}

# `explicitos` guarda se **alguma** fonte da lista carregou de verdade, e não se
# a lista existe. A diferença importa desde que o campo passou a vir
# pré-preenchido: num robô onde o caminho padrão não existe, a lista inteira
# falha, e aí a situação é idêntica à de campo vazio — a busca automática tem
# que valer. Sem isso, um padrão errado desligaria a descoberta para todo mundo.
explicitos=''

if [ -n "$setups" ]; then
    # IFS de newline para caminho com espaço continuar inteiro, e `set -f` para
    # caminho com `*` não virar glob antes de ser lido.
    ifs_antigo=$IFS
    IFS='
'
    set -f
    for s in $setups; do
        if carregar "$s"; then
            explicitos=sim
        else
            echo "não consegui carregar: $s" >&2
        fi
    done
    set +f
    IFS=$ifs_antigo
fi

# Camada 2: a distro do sistema. Só entra se as fontes explícitas não trouxeram
# ROS nenhum — um overlay de colcon já carrega o underlay dele junto, e sourcear
# /opt/ros por cima inverteria a precedência do AMENT_PREFIX_PATH.
if [ -z "$ROS_DISTRO" ]; then
    for s in /opt/ros/*/setup.bash; do
        carregar "$s" && break
    done
fi

# Camada 3: overlays de workspace. Sem eles as mensagens do SDK aparecem com
# tipo que não importa e não podem ser assinadas (§5) — mas isso é aviso, não
# erro. Quando alguma fonte da lista carregou, a lista é a receita completa: não
# carregamos overlay por cima do que ele escolheu, só dizemos o que existe, para
# ele decidir acrescentar.
#
# Vem antes de desistir: em robô que só tem ROS dentro do workspace, é aqui que
# o ambiente aparece.
for o in "$HOME"/*_ws/install/setup.bash; do
    [ -r "$o" ] || continue
    case " $carregados" in *" $o "*) continue ;; esac
    if [ -n "$explicitos" ]; then
        echo "overlay disponível, não carregado: $o" >&2
    else
        carregar "$o"
    fi
done

if [ -z "$carregados" ]; then
    printf 'nenhum ambiente ROS carregado. Procurado em:%b\n' "$tentados" >&2
    exit 3
fi

command -v python3 >/dev/null 2>&1 || {
    echo 'python3 não encontrado no robô' >&2
    exit 4
}

# Substituição de comando, não pipe: o stdin precisa ficar livre para ser o
# canal do protocolo.
exec python3 -c "$(printf %s "$agent_b64" | base64 -d)"
"""


def parse_target(target):
    """Divide "user@host[:porta]". Levanta ValueError se não der para usar."""
    target = target.strip()
    if not target:
        raise ValueError("Nenhum host informado.")

    user, sep, resto = target.partition("@")
    if not sep:
        raise ValueError("Falta o usuário, use usuario@host.")
    if not user:
        raise ValueError("Falta o usuário antes do '@'.")

    host, sep, porta = resto.partition(":")
    if not host:
        raise ValueError("Falta o host depois do '@'.")
    if sep:
        try:
            porta = int(porta)
        except ValueError:
            raise ValueError(f"Porta deve ser número, veio {porta!r}.") from None
    else:
        porta = DEFAULT_PORT

    return user, host, porta


def fontes_de_setup(setup):
    """Texto do campo *Setup ROS* → lista de caminhos, na ordem digitada.

    Uma fonte por linha. Linha vazia e linha de comentário são descartadas, para
    que o campo aguente ser um bloco anotado ("# só o overlay do SDK") em vez de
    exigir uma linha limpa.
    """
    if not setup:
        return []
    linhas = setup.splitlines() if isinstance(setup, str) else list(setup)

    fontes = []
    for linha in linhas:
        linha = linha.strip()
        if not linha or linha.startswith("#"):
            continue
        # O caminho entra no script dentro de aspa simples; uma aspa simples no
        # meio fecharia a string e o resto viraria comando.
        if "'" in linha:
            raise ValueError(f"Caminho do setup não pode conter aspa simples: {linha}")
        fontes.append(linha)
    return fontes


def _remote_command(setup=""):
    """Monta o comando único que o ssh executa no robô."""
    agent = Path(__file__).with_name("agent.py").read_bytes()
    agent_b64 = base64.b64encode(agent).decode("ascii")

    setups = "\n".join(fontes_de_setup(setup))
    script = BOOTSTRAP.replace("@@SETUPS@@", setups).replace("@@AGENT@@", agent_b64)
    script_b64 = base64.b64encode(script.encode("utf-8")).decode("ascii")

    # Novamente substituição de comando, para o stdin do bash não ser consumido
    # pelo próprio script.
    return f'bash -c "$(printf %s {script_b64} | base64 -d)"'


class SshSession(QtCore.QObject):
    """Uma sessão: abre o SSH, sobe o agente, entrega mensagens do protocolo."""

    connected = QtCore.Signal(str, str)  # ros_distro, host remoto
    message = QtCore.Signal(dict)
    log = QtCore.Signal(str)
    failed = QtCore.Signal(str)
    closed = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.proc = None
        self._buf = b""
        self._stderr = []
        self._pronto = False
        self._askpass_dir = None
        self._host = ""
        self._porta = DEFAULT_PORT
        self._hello_timer = QtCore.QTimer(self)
        self._hello_timer.setSingleShot(True)
        self._hello_timer.setInterval(HELLO_TIMEOUT)
        self._hello_timer.timeout.connect(self._on_hello_timeout)
        self._keepalive = QtCore.QTimer(self)
        self._keepalive.setInterval(KEEPALIVE)
        self._keepalive.timeout.connect(lambda: self.send({"cmd": "ping"}))

    # -- ciclo de vida ----------------------------------------------------

    def start(self, target, password="", setup=""):
        try:
            user, host, porta = parse_target(target)
            comando = _remote_command(setup)
        except ValueError as exc:
            self.failed.emit(str(exc))
            return

        self._host, self._porta = host, porta
        self._buf = b""
        self._stderr = []
        self._pronto = False

        args = [
            "-T",  # sem tty: stdout precisa chegar limpo
            "-p", str(porta),
            "-o", f"ConnectTimeout={CONNECT_TIMEOUT}",
            "-o", "ServerAliveInterval=15",
            "-o", "LogLevel=ERROR",
            # Host desconhecido falha em vez de perguntar — com askpass ativo,
            # uma pergunta de host key seria respondida com a senha.
            "-o", "StrictHostKeyChecking=yes",
        ]
        if password:
            args += ["-o", "NumberOfPasswordPrompts=1"]
        else:
            args += ["-o", "BatchMode=yes"]
        args += [f"{user}@{host}", comando]

        self.proc = QtCore.QProcess(self)
        self.proc.setProgram("ssh")
        self.proc.setArguments(args)
        self.proc.setProcessEnvironment(self._environment(password))
        self.proc.readyReadStandardOutput.connect(self._on_stdout)
        self.proc.readyReadStandardError.connect(self._on_stderr)
        self.proc.finished.connect(self._on_finished)
        self.proc.errorOccurred.connect(self._on_proc_error)

        self._hello_timer.start()
        self.proc.start()

    def send(self, cmd):
        """Manda um comando para o agente (§4, GUI → agente)."""
        if not self._pronto or self.proc is None:
            return
        self.proc.write((json.dumps(cmd, ensure_ascii=False) + "\n").encode("utf-8"))

    def stop(self):
        """Fecha a sessão. O agente morre com o EOF (§1)."""
        self._hello_timer.stop()
        self._keepalive.stop()
        if self.proc is not None and self.proc.state() != QtCore.QProcess.NotRunning:
            self.proc.closeWriteChannel()
            if not self.proc.waitForFinished(3000):
                self.proc.kill()
                self.proc.waitForFinished(1000)
        self._limpar_askpass()

    # -- senha ------------------------------------------------------------

    def _environment(self, password):
        env = QtCore.QProcessEnvironment.systemEnvironment()
        if not password:
            return env

        # A senha vai por variável de ambiente do processo filho, não na linha
        # de comando: `sshpass -p` deixaria a senha visível no `ps` para
        # qualquer usuário da máquina.
        self._askpass_dir = tempfile.mkdtemp(prefix="t1debug-")
        helper = Path(self._askpass_dir, "askpass.sh")
        helper.write_text('#!/bin/sh\nprintf %s "$T1_DEBUG_PASSWORD"\n')
        helper.chmod(stat.S_IRWXU)

        env.insert("T1_DEBUG_PASSWORD", password)
        env.insert("SSH_ASKPASS", str(helper))
        env.insert("SSH_ASKPASS_REQUIRE", "force")
        if not env.contains("DISPLAY"):
            env.insert("DISPLAY", ":0")  # exigido por versões antigas do OpenSSH
        return env

    def _limpar_askpass(self):
        if self._askpass_dir:
            shutil.rmtree(self._askpass_dir, ignore_errors=True)
            self._askpass_dir = None

    # -- leitura ----------------------------------------------------------

    def _on_stdout(self):
        self._buf += bytes(self.proc.readAllStandardOutput())
        while b"\n" in self._buf:
            linha, self._buf = self._buf.split(b"\n", 1)
            linha = linha.strip()
            if not linha:
                continue
            try:
                msg = json.loads(linha.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                self.log.emit(f"linha não-JSON no stdout: {linha[:120]!r}")
                continue
            self._on_message(msg)

    def _on_message(self, msg):
        if not self._pronto:
            if msg.get("tipo") == "status" and msg.get("nivel") == "erro":
                self._falhar(msg.get("msg", "erro no agente"))
                return
            if msg.get("tipo") != "hello":
                self.log.emit(f"mensagem antes do hello, ignorada: {msg.get('tipo')!r}")
                return

            versao = msg.get("v")
            if versao != PROTOCOL_VERSION:
                self._falhar(
                    f"Protocolo incompatível: agente fala v{versao}, GUI fala "
                    f"v{PROTOCOL_VERSION}. Atualize os dois lados."
                )
                return

            self._pronto = True
            self._hello_timer.stop()
            self._keepalive.start()
            self._limpar_askpass()
            self.connected.emit(
                msg.get("ros_distro", "desconhecido"),
                msg.get("host", self._host),
            )
            return

        self.message.emit(msg)

    def _on_stderr(self):
        texto = bytes(self.proc.readAllStandardError()).decode("utf-8", "replace")
        for linha in texto.splitlines():
            if linha.strip():
                self._stderr.append(linha)
                self.log.emit(linha)
        del self._stderr[:-40]

    # -- falhas -----------------------------------------------------------

    def _on_proc_error(self, erro):
        if erro == QtCore.QProcess.FailedToStart:
            self._falhar("Não achei o comando `ssh` no PATH deste PC.")

    def _on_hello_timeout(self):
        self._falhar(
            f"O agente não respondeu em {HELLO_TIMEOUT // 1000}s. "
            "Veja o painel de diagnóstico."
        )
        self.stop()

    def _on_finished(self, codigo, _status):
        self._keepalive.stop()
        self._limpar_askpass()
        if self._pronto:
            self.closed.emit()
        else:
            self._falhar(self._diagnostico(codigo))

    def _falhar(self, msg):
        self._hello_timer.stop()
        self._limpar_askpass()
        if not self._pronto:
            self.failed.emit(msg)

    def _diagnostico(self, codigo):
        """Traduz saída do ssh em algo que dá para agir.

        O `stderr` não entra nas mensagens: ele já foi para o painel de
        diagnóstico ao vivo, e repetir aqui só duplica.
        """
        baixo = "\n".join(self._stderr).lower()

        if codigo == 3:
            return (
                f"Nenhum ambiente ROS encontrado em {self._host}. "
                "Informe os setup.bash no campo Setup ROS, um por linha."
            )
        if codigo == 4:
            return f"python3 não existe em {self._host}."
        if codigo == 127:
            return f"{self._host} não tem `bash` ou `base64` no PATH."

        if "permission denied" in baixo:
            return "Autenticação recusada. Confira usuário e senha."
        if "host key verification failed" in baixo or "not known" in baixo:
            return (
                f"{self._host} não está no ~/.ssh/known_hosts. Conecte uma vez "
                f"com `ssh {self._host}` para conferir e registrar a host key."
            )
        if "could not resolve" in baixo:
            return f"Não consigo resolver o host {self._host}."
        if "connection refused" in baixo:
            return f"Conexão recusada por {self._host}:{self._porta}."
        if "timed out" in baixo or "timeout" in baixo:
            return f"Tempo esgotado conectando em {self._host}:{self._porta}."

        return f"ssh terminou com código {codigo}."
