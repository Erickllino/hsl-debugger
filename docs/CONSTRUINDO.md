# Construindo o T1 Debug do zero

Guia passo a passo para reconstruir a ferramenta sozinho, entendendo cada
decisão. Cada passo termina num **checkpoint** que você roda e vê funcionar
antes de seguir — se um checkpoint falhar, o problema está no passo atual, não
três passos atrás.

A arquitetura e o *porquê* das decisões estão em [`T1_DEBUG.md`](T1_DEBUG.md).
Este documento é o *como*.

**Pré-requisitos:** Python 3.13, [uv](https://docs.astral.sh/uv/), cliente `ssh`.
**Não é pré-requisito:** ROS 2 no seu PC, nem qualquer instalação no robô.

Você consegue fazer os passos 0 a 6 inteiros sem robô nenhum — o passo 7 ensina
a simular um.

---

## A ideia em uma frase

> A GUI abre uma sessão SSH, manda um script Python para o robô, e conversa com
> ele pelo próprio canal do SSH.

```
[ GUI PySide6 no PC ]  ⇄  ssh  ⇄  [ agent.py no robô ]  ⇄  DDS  ⇄  [ nós ROS 2 ]
        stdin/stdout                 processo efêmero
```

O SSH não é só autenticação, **ele é o cano por onde os dados passam**. O agente
escreve JSON no `stdout`, a GUI lê. A GUI escreve comandos no `stdin`, o agente
lê. Não existe porta TCP, túnel, nem daemon.

Guarde essa frase: o resto do guia é consequência dela.

---

## Passo 0 — esqueleto

```bash
uv init hsl-debugger
cd hsl-debugger
uv add pyside6
mkdir src docs
```

No fim você vai ter:

```
main.py              # ponto de entrada, 10 linhas
src/menu.py          # a janela
src/connection.py    # a sessão SSH (roda no PC)
src/agent.py         # o agente (roda no robô)
docs/
```

`src/agent.py` é o único arquivo que **não** roda no seu PC. Ele é lido como
texto e enviado para o robô. Isso vai importar no passo 4.

---

## Passo 1 — a janela

Comece pelo que dá para ver. `src/menu.py`:

```python
from PySide6 import QtCore, QtWidgets


class MyWidget(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("T1 Debug")

        self.ssh = QtWidgets.QLineEdit()
        self.ssh.setPlaceholderText("usuario@robo")

        self.password = QtWidgets.QLineEdit()
        self.password.setPlaceholderText("senha (vazio = chave SSH)")
        self.password.setEchoMode(QtWidgets.QLineEdit.Password)

        self.button = QtWidgets.QPushButton("Conectar")
        self.button.setDefault(True)

        form = QtWidgets.QFormLayout()
        form.addRow("SSH", self.ssh)
        form.addRow("Senha", self.password)
        form.addRow("", self.button)

        box = QtWidgets.QWidget()
        box.setLayout(form)
        box.setMaximumWidth(460)

        outer = QtWidgets.QVBoxLayout(self)
        outer.addStretch()
        outer.addWidget(box, alignment=QtCore.Qt.AlignHCenter)
        outer.addStretch()

        self.button.clicked.connect(self.magic)

    @QtCore.Slot()
    def magic(self):
        print(self.ssh.text().strip())
```

E `main.py`:

```python
import sys
from PySide6 import QtWidgets
import src.menu as menu

if __name__ == "__main__":
    app = QtWidgets.QApplication([])
    widget = menu.MyWidget()
    widget.resize(800, 600)
    widget.show()
    sys.exit(app.exec())
```

### Duas armadilhas que valem conhecer

**1. Nunca chame um atributo de `self.layout`.**

```python
self.layout = QtWidgets.QGridLayout(self)   # ERRADO
```

`QWidget.layout()` é um método herdado. Atribuir a `self.layout` substitui o
método por um objeto, e qualquer código que chamar `widget.layout()` — seu ou de
uma biblioteca — quebra com `QGridLayout is not callable`. É um bug que só
aparece muito depois. Use `outer`, `form`, `grid`, qualquer nome menos esse.

**2. Campo de senha precisa de `setEchoMode`.** Sem ele o `QLineEdit` mostra a
senha em texto claro na tela.

Uma terceira, de layout: passar `self` no construtor do layout
(`QVBoxLayout(self)`) já o instala no widget. Chamar `setLayout()` depois é
redundante e o Qt reclama no terminal.

### Checkpoint 1

```bash
uv run python main.py
```

Janela abre, formulário centralizado, senha aparece como bolinhas, o botão
imprime o alvo no terminal.

---

## Passo 2 — o insight central: o `stdin` é sagrado

Antes de escrever qualquer código de rede, entenda o problema que define toda a
arquitetura.

Você quer rodar um script Python no robô sem instalar nada lá. O reflexo natural
é mandar o script pelo `stdin`:

```bash
cat agent.py | ssh usuario@robo python3 -      # parece esperto
```

Funciona uma vez — e destrói o projeto. Porque o `stdin` do agente **é o canal de
comandos da GUI**. Se ele foi gasto entregando o script, não sobra nada para o
protocolo, e o agente vê EOF imediatamente e morre.

O mesmo vale para o script de bootstrap:

```bash
ssh usuario@robo 'echo <BASE64> | base64 -d | bash'   # mesmo erro, disfarçado
```

O `bash` aí recebe o pipe como `stdin`, e todo processo que ele iniciar herda
esse pipe já esvaziado.

### A saída: substituição de comando

```sh
ssh -T usuario@robo 'bash -c "$(printf %s <BOOTSTRAP_B64> | base64 -d)"'
```

`$(...)` roda o `printf | base64` num subshell **separado**, captura a saída como
texto, e entrega esse texto ao `bash -c` como argumento. O `stdin` do `bash`
nunca é tocado — continua sendo o socket do SSH.

O mesmo truque de novo, um nível abaixo, para o Python:

```sh
exec python3 -c "$(printf %s "$agent_b64" | base64 -d)"
```

`exec` substitui o processo do shell pelo do Python, em vez de deixar um shell
pai pendurado.

### Checkpoint 2

Veja a diferença com as próprias mãos, sem SSH nenhum. O `printf` da esquerda faz
o papel do canal do SSH trazendo dados; o `sh -c` faz o papel do shell remoto:

```bash
S='read -r x; echo "li: [$x]"'

# ERRADO: o script chega por pipe, e o canal original se perde
printf 'oi mundo\n' | sh -c "echo '$S' | bash"

# CERTO: o script chega como argumento, e o canal continua sendo o stdin
printf 'oi mundo\n' | sh -c "bash -c \"\$(echo '$S')\""
```

O primeiro imprime `li: []` — o `read` não tem de onde ler, porque o `stdin` do
`bash` é o pipe do `echo`, não o canal de fora. O segundo imprime
`li: [oi mundo]`.

Essa diferença de uma linha é o projeto inteiro. Se você errar aqui, o sintoma
lá na frente vai ser "o agente morre sozinho assim que sobe" — e você vai
procurar o bug no lugar errado.

---

## Passo 3 — o agente

Agora `src/agent.py`, o programa que roda no robô. Três regras, e elas não são
negociáveis:

| Regra | Motivo |
|---|---|
| `stdout` só carrega protocolo | um `print` de debug corrompe a sessão |
| logs vão para `stderr` | é o canal separado que a GUI mostra no painel |
| somente leitura | robô emprestado: nada de publicar, chamar serviço ou escrever parâmetro |

Comece pelas duas funções de saída:

```python
import json, os, socket, sys, threading, time

PROTOCOL_VERSION = 1
GRAPH_PERIOD = 1.0
IDLE_TIMEOUT = 600.0

_stdout_lock = threading.Lock()


def emit(tipo, **campos):
    """Escreve uma mensagem do protocolo: JSON de uma linha, UTF-8."""
    campos["v"] = PROTOCOL_VERSION
    campos["tipo"] = tipo
    linha = json.dumps(campos, ensure_ascii=False)
    with _stdout_lock:
        sys.stdout.write(linha + "\n")
        sys.stdout.flush()


def log(msg):
    sys.stderr.write(f"[agent] {msg}\n")
    sys.stderr.flush()
```

Três detalhes que parecem pequenos:

- **`flush()` sempre.** Sem tty, o Python usa buffer de 8 KB. Suas mensagens
  ficariam presas por segundos e o `hello` nunca chegaria a tempo.
- **O lock.** A thread de comandos e a thread principal escrevem no mesmo
  `stdout`; sem lock duas linhas se intercalam e viram JSON inválido.
- **`v` desde o primeiro dia.** Alguém do time sempre estará com a GUI da semana
  passada. O campo de versão transforma "falha obscura" em "atualize os dois
  lados".

### O grafo

```python
def snapshot(node):
    nos = [
        {"nome": nome, "ns": ns}
        for nome, ns in node.get_node_names_and_namespaces()
    ]

    topicos = []
    for nome, tipos in node.get_topic_names_and_types():
        try:
            pubs = [i.node_name for i in node.get_publishers_info_by_topic(nome)]
            subs = [i.node_name for i in node.get_subscriptions_info_by_topic(nome)]
        except Exception as exc:
            # Tópico pode sumir entre a listagem e a consulta.
            log(f"endpoints de {nome} indisponíveis: {exc}")
            pubs, subs = [], []
        topicos.append({"nome": nome, "tipos": tipos, "pubs": pubs, "subs": subs})

    return nos, topicos
```

Snapshot completo a cada segundo, não diff. Gasta mais bytes e poupa muito
estado para sincronizar dos dois lados.

### Comandos e morte por EOF

```python
class Commands(threading.Thread):
    """Lê comandos da GUI. EOF no stdin é o sinal de encerramento."""

    daemon = True

    def __init__(self):
        super().__init__()
        self.parar = threading.Event()
        self.ultimo = time.monotonic()

    def run(self):
        for linha in sys.stdin:
            linha = linha.strip()
            if not linha:
                continue
            self.ultimo = time.monotonic()
            try:
                cmd = json.loads(linha)
            except ValueError:
                log(f"comando ilegível, ignorado: {linha[:120]!r}")
                continue
            self.despachar(cmd)

        log("EOF no stdin, encerrando")
        self.parar.set()

    def despachar(self, cmd):
        nome = cmd.get("cmd")
        if nome == "ping":
            emit("pong")
        else:
            emit("status", nivel="aviso", msg=f"comando desconhecido: {nome!r}")
```

O `for linha in sys.stdin` termina sozinho quando o SSH cai. **É assim que o
agente morre** — sem systemd, sem PID file, sem código de limpeza. Feche a
janela e o processo some do robô. Esse comportamento de graça é a razão de usar
`stdin`/`stdout` em vez de uma porta TCP.

O `IDLE_TIMEOUT` é o cinto de segurança para o caso do SSH travar sem fechar.

### O laço principal

```python
def main():
    try:
        import rclpy
    except ImportError as exc:
        emit("status", nivel="erro", msg=f"rclpy indisponível: {exc}")
        return 3

    rclpy.init(args=[])
    node = rclpy.create_node("t1_debug_agent")

    emit("hello",
         ros_distro=os.environ.get("ROS_DISTRO", "desconhecido"),
         host=socket.gethostname(),
         pid=os.getpid())

    comandos = Commands()
    comandos.start()

    proximo_grafo = 0.0
    try:
        while not comandos.parar.is_set():
            rclpy.spin_once(node, timeout_sec=0.1)
            agora = time.monotonic()
            if agora >= proximo_grafo:
                nos, topicos = snapshot(node)
                emit("grafo", nos=nos, topicos=topicos)
                proximo_grafo = agora + GRAPH_PERIOD
            if agora - comandos.ultimo > IDLE_TIMEOUT:
                log(f"ocioso por {IDLE_TIMEOUT:.0f}s, encerrando")
                break
    finally:
        node.destroy_node()
        rclpy.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

O `import rclpy` fica **dentro** de `main()`, não no topo. Assim o erro vira uma
mensagem de protocolo que a GUI sabe mostrar, em vez de um traceback no `stderr`
que ninguém lê.

### Checkpoint 3

Você não tem ROS, então crie um `rclpy` de mentira:

```bash
mkdir -p /tmp/fakeros/lib
cat > /tmp/fakeros/lib/rclpy.py <<'PY'
class _Ep:
    def __init__(self, n): self.node_name = n
class _Node:
    def get_node_names_and_namespaces(self): return [("t1_ctrl","/")]
    def get_topic_names_and_types(self): return [("/imu",["sensor_msgs/msg/Imu"])]
    def get_publishers_info_by_topic(self, t): return [_Ep("t1_ctrl")]
    def get_subscriptions_info_by_topic(self, t): return []
    def destroy_node(self): pass
def init(args=None): pass
def create_node(name): return _Node()
def spin_once(node, timeout_sec=0.1):
    import time; time.sleep(timeout_sec)
def shutdown(): pass
PY

printf '{"cmd":"ping"}\n' | PYTHONPATH=/tmp/fakeros/lib uv run python src/agent.py
```

Você deve ver `hello`, um `pong`, um ou mais `grafo`, e
`[agent] EOF no stdin, encerrando`. Note que os logs `[agent]` e o JSON estão
misturados só porque o terminal junta os dois canais — redirecione com
`2>/dev/null` e sobra JSON puro. Esse é exatamente o efeito que a GUI aproveita.

---

## Passo 4 — achar o ROS no robô (a parte chata)

Esta é a única parte realmente irritante, e é onde o tempo vai.

**SSH não-interativo não executa o `.bashrc`.** O comando cai num shell pelado:
`import rclpy` falha, e as mensagens do SDK do Booster não estão no
`AMENT_PREFIX_PATH`. Em robô próprio você sabe o caminho. Em emprestado, não.

A resposta é um script de bootstrap que procura em camadas. Em `connection.py`:

```python
BOOTSTRAP = r"""
setup_explicito='@@SETUP@@'
agent_b64='@@AGENT@@'

tentados=''
carregado=''

carregar() {
    tentados="$tentados\n    $1"
    [ -r "$1" ] || return 1
    . "$1" >/dev/null 2>&1 || return 1
    carregado="$1"
    return 0
}

if [ -n "$setup_explicito" ]; then
    carregar "$setup_explicito"
else
    for s in /opt/ros/*/setup.bash; do
        carregar "$s" && break
    done
fi

if [ -z "$carregado" ]; then
    printf 'nenhum ambiente ROS carregado. Procurado em:%b\n' "$tentados" >&2
    exit 3
fi
echo "ambiente: $carregado" >&2

for o in "$HOME"/*_ws/install/setup.bash; do
    [ -r "$o" ] && . "$o" >/dev/null 2>&1 && echo "overlay: $o" >&2
done

command -v python3 >/dev/null 2>&1 || {
    echo 'python3 não encontrado no robô' >&2
    exit 4
}

exec python3 -c "$(printf %s "$agent_b64" | base64 -d)"
"""
```

Pontos de atenção:

- **Nada de `set -u`.** Os `setup.bash` do ROS usam variáveis não definidas e
  morrem com ele ligado.
- **Códigos de saída são a interface.** `3` = sem ambiente ROS, `4` = sem
  `python3`. A GUI traduz cada um numa mensagem diferente. Escolher códigos
  próprios é mais confiável que adivinhar pelo texto do `stderr`.
- **Overlay não é opcional na prática.** Sem ele os tópicos do SDK aparecem com
  tipo desconhecido e não podem ser assinados — mas isso é aviso, não erro.
- **Tudo informativo vai para `stderr`** (`>&2`), porque o `stdout` já pertence
  ao protocolo.

E o empacotamento:

```python
def _remote_command(setup_path=""):
    agent = Path(__file__).with_name("agent.py").read_bytes()
    agent_b64 = base64.b64encode(agent).decode("ascii")

    if "'" in setup_path:
        raise ValueError("Caminho do setup não pode conter aspa simples.")

    script = BOOTSTRAP.replace("@@SETUP@@", setup_path).replace("@@AGENT@@", agent_b64)
    script_b64 = base64.b64encode(script.encode("utf-8")).decode("ascii")
    return f'bash -c "$(printf %s {script_b64} | base64 -d)"'
```

Base64 nos dois níveis existe para não ter que escapar nada. O script tem aspas,
`$`, `*` e quebras de linha; passar isso por cima de `ssh` → shell do usuário →
`bash -c` sem codificar é uma fonte infinita de bugs. Em base64, é um blob de
`[A-Za-z0-9+/=]` que atravessa qualquer shell intacto.

O `agent.py` inteiro em base64 dá ~9 KB de linha de comando. O limite (`ARG_MAX`)
é da ordem de 2 MB, então há folga de sobra.

A verificação da aspa simples é necessária porque `setup_explicito='@@SETUP@@'`
está entre aspas simples no script: um `'` no caminho fecharia a string e o
resto viraria comando.

### Checkpoint 4

Rode o comando remoto **na sua própria máquina**, como se ela fosse o robô:

```bash
uv run python -c "
from src.connection import _remote_command
open('/tmp/remote.sh','w').write(_remote_command())
"
sh /tmp/remote.sh </dev/null; echo "exit=$?"
```

Sem ROS instalado você vê a mensagem "nenhum ambiente ROS carregado" e
`exit=3`. Isso é sucesso: o bootstrap rodou e falhou no lugar certo.

Agora com o ambiente falso do passo 3:

```bash
cat > /tmp/fakeros/setup.bash <<'PY'
export ROS_DISTRO=humble
export PYTHONPATH=/tmp/fakeros/lib:$PYTHONPATH
PY

uv run python -c "
from src.connection import _remote_command
open('/tmp/remote_ok.sh','w').write(_remote_command('/tmp/fakeros/setup.bash'))
"
{ printf '{"cmd":"ping"}\n'; sleep 2; } | sh /tmp/remote_ok.sh
```

(Aspas simples em volta, sem escapar as duplas. Se você escrever `\"` aí dentro,
a barra vai literal, o agente recebe JSON inválido e responde
`comando ilegível, ignorado` em vez de `pong` — um jeito fácil de perder tempo
achando que o protocolo está quebrado.)

Agora sai `hello`, `pong` e `grafo` — o agente inteiro subiu através do
bootstrap, e o `stdin` sobreviveu à viagem. O passo 2 provado na prática.

---

## Passo 5 — a sessão SSH no PC

`SshSession` é um `QObject` que embrulha um `QProcess` rodando `ssh`. Nenhum
widget é tocado aqui: a comunicação com a interface é **só por `Signal`**.

```python
class SshSession(QtCore.QObject):
    connected = QtCore.Signal(str, str)  # ros_distro, host remoto
    message = QtCore.Signal(dict)
    log = QtCore.Signal(str)
    failed = QtCore.Signal(str)
    closed = QtCore.Signal()
```

Por que `QProcess` e não uma biblioteca SSH em Python: a stack do projeto é
PySide6 + biblioteca padrão, e o `ssh` do sistema já lê `~/.ssh/config`,
`known_hosts` e o agente de chaves do usuário. Você herda tudo isso de graça.

Os argumentos, cada um por um motivo:

```python
args = [
    "-T",                                   # sem tty: tty ecoa e mangla o stdout
    "-p", str(porta),
    "-o", f"ConnectTimeout={CONNECT_TIMEOUT}",
    "-o", "ServerAliveInterval=15",         # detecta robô que sumiu do wifi
    "-o", "LogLevel=ERROR",                 # menos ruído no painel
    "-o", "StrictHostKeyChecking=yes",      # ver passo 6
]
if password:
    args += ["-o", "NumberOfPasswordPrompts=1"]   # falha rápido em senha errada
else:
    args += ["-o", "BatchMode=yes"]               # nunca trava esperando prompt
args += [f"{user}@{host}", comando]
```

O `-T` é fácil de esquecer e o sintoma é confuso: com tty, o terminal remoto
ecoa o que você escreve e converte `\n` em `\r\n`, então o JSON chega sujo.

### Ler linha a linha

`readyReadStandardOutput` dispara com pedaços arbitrários, não com linhas. Uma
mensagem JSON pode chegar partida em duas leituras, e duas mensagens podem
chegar juntas. Precisa de buffer:

```python
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
```

Se você processar `readAll()` direto sem acumular, funciona nos testes locais
(mensagens pequenas, rápidas) e quebra no robô real. Esse é o tipo de bug que
some quando você vai depurar.

### O handshake

Antes do `hello`, a sessão não está pronta. Depois dele, tudo vira `message`:

```python
def _on_message(self, msg):
    if not self._pronto:
        if msg.get("tipo") == "status" and msg.get("nivel") == "erro":
            self._falhar(msg.get("msg", "erro no agente"))
            return
        if msg.get("tipo") != "hello":
            return
        if msg.get("v") != PROTOCOL_VERSION:
            self._falhar(f"Protocolo incompatível: agente v{msg.get('v')}, "
                         f"GUI v{PROTOCOL_VERSION}. Atualize os dois lados.")
            return
        self._pronto = True
        self._hello_timer.stop()
        self.connected.emit(msg.get("ros_distro", "?"), msg.get("host", self._host))
        return

    self.message.emit(msg)
```

O `_hello_timer` é um `QTimer` de tiro único (45 s) armado no `start()`. Sem ele,
um robô que aceita SSH mas trava carregando o ROS deixa a GUI em "conectando…"
para sempre.

### Traduzir falhas

Código de saída + `stderr` viram mensagem acionável:

```python
def _diagnostico(self, codigo):
    baixo = "\n".join(self._stderr).lower()
    if codigo == 3:
        return (f"Nenhum ambiente ROS encontrado em {self._host}. "
                "Informe o caminho do setup.bash acima.")
    if codigo == 4:
        return f"python3 não existe em {self._host}."
    if "permission denied" in baixo:
        return "Autenticação recusada. Confira usuário e senha."
    if "host key verification failed" in baixo or "not known" in baixo:
        return (f"{self._host} não está no ~/.ssh/known_hosts. Conecte uma vez "
                f"com `ssh {self._host}` para conferir e registrar a host key.")
    if "could not resolve" in baixo:
        return f"Não consigo resolver o host {self._host}."
    return f"ssh terminou com código {codigo}."
```

Vale o esforço: "Autenticação recusada, confira usuário e senha" economiza
quinze minutos por pessoa por semana comparado a `ssh terminou com código 255`.

---

## Passo 6 — senha sem vazar

Se você só usa chave SSH, pule: é o caminho recomendado de verdade.

```bash
ssh-keygen -t ed25519
ssh-copy-id usuario@robo
```

Mas aceitar senha digitada é preciso, e aí tem duas armadilhas.

**Nunca `sshpass -p $senha`.** Argumentos de processo são públicos: qualquer
usuário da máquina roda `ps aux` e lê a senha do robô. Use `SSH_ASKPASS` — o
OpenSSH executa um programa auxiliar para pedir a senha:

```python
self._askpass_dir = tempfile.mkdtemp(prefix="t1debug-")
helper = Path(self._askpass_dir, "askpass.sh")
helper.write_text('#!/bin/sh\nprintf %s "$T1_DEBUG_PASSWORD"\n')
helper.chmod(stat.S_IRWXU)          # 0700, só o dono lê e executa

env.insert("T1_DEBUG_PASSWORD", password)
env.insert("SSH_ASKPASS", str(helper))
env.insert("SSH_ASKPASS_REQUIRE", "force")   # use o helper mesmo tendo tty
if not env.contains("DISPLAY"):
    env.insert("DISPLAY", ":0")              # OpenSSH antigo exige
```

O helper não contém segredo — só lê variável de ambiente do processo filho, que
é visível apenas para o mesmo usuário e para o root. Apague o diretório assim
que o `hello` chegar.

**A segunda armadilha é sutil e vale entender.** Com `SSH_ASKPASS` armado, o
askpass responde *qualquer* pergunta do ssh — inclusive
`Are you sure you want to continue connecting?` numa host key nova. Ou seja: no
modo padrão (`ask`), o cliente mandaria **a senha do robô como resposta à
pergunta de host key**. Por isso:

```python
"-o", "StrictHostKeyChecking=yes",
```

Host desconhecido falha na hora, com instrução para o usuário conferir e
registrar a chave com um `ssh` normal antes. Chato uma vez, correto sempre.

E o que nunca existe: caixinha "lembrar senha". Basta ela existir para que em
poucos meses haja senha de robô — inclusive de robô emprestado — em texto plano
no disco de meio time. Perfis salvos guardam host, usuário, porta, caminho do
setup e apelido. Nunca segredo.

---

## Passo 7 — ligar na GUI

Agora o `magic()` de verdade. Ele **não bloqueia**: dispara e volta.

```python
@QtCore.Slot()
def magic(self):
    if self.session is not None:
        self.desconectar()
        return

    self.diagnostico.setVisible(True)
    self.set_busy(True)
    self.status.setText("conectando…")

    self.session = SshSession(self)
    self.session.connected.connect(self.on_connected)
    self.session.failed.connect(self.on_failed)
    self.session.log.connect(self.on_log)
    self.session.message.connect(self.on_message)
    self.session.closed.connect(self.on_closed)
    self.session.start(
        self.ssh.text().strip(),
        self.password.text(),
        self.setup.text().strip(),
    )
```

Compare com o reflexo inicial, que quase todo mundo escreve primeiro:

```python
os.system(f'ssh {self.ssh.text()}')     # não faça isso
```

Três problemas: congela a interface até o comando terminar; interpola texto do
usuário direto num shell (um `;` no campo executa o que vier depois); e não há
como ler a saída, que é justamente o ponto da ferramenta.

Os retornos:

```python
@QtCore.Slot(str, str)
def on_connected(self, distro, host):
    self.setWindowTitle(f"T1 Debug — {host}")     # em qual máquina eu estou
    self.status.setText(f"conectado a {host} (ROS {distro})")
    self.password.clear()                          # senha some da memória da GUI
    self.button.setText("Desconectar")

@QtCore.Slot(str)
def on_log(self, linha):
    self.diagnostico.appendPlainText(linha)        # stderr ao vivo
```

Dois detalhes que não são enfeite:

- **O host no título e no rodapé, sempre.** Com dois robôs próprios mais
  emprestados eventuais, "em qual máquina eu estou" é informação de primeira
  classe. Rodar um teste achando que está no T1-01 quando está no T1-02 custa
  caro.
- **O painel de diagnóstico** existe porque o passo 4 vai falhar em robô novo, e
  o `stderr` é onde está a resposta.

Fechar a janela precisa derrubar o SSH:

```python
def closeEvent(self, event):
    self.desconectar()      # closeWriteChannel → EOF → agente morre
    super().closeEvent(event)
```

---

## Passo 8 — testar tudo sem robô

Você já tem `rclpy` falso e `setup.bash` falso. Falta o `ssh` falso: um script
que ignora os argumentos de rede e executa o comando remoto localmente.

```bash
mkdir -p /tmp/fakebin
cat > /tmp/fakebin/ssh <<'SH'
#!/bin/sh
for a; do last="$a"; done      # o comando remoto é sempre o último argumento
exec sh -c "$last"
SH
chmod +x /tmp/fakebin/ssh
```

Com ele no `PATH`, a GUI faz o caminho inteiro — monta o comando, "conecta",
sobe o agente, recebe o `hello` — contra a sua própria máquina:

```bash
PATH=/tmp/fakebin:$PATH QT_QPA_PLATFORM=offscreen uv run python - <<'PY'
from PySide6 import QtCore, QtWidgets
import src.menu as menu

app = QtWidgets.QApplication([])
w = menu.MyWidget(); w.show()
w.ssh.setCurrentText("eu@robo-falso")   # combo editável: guarda o histórico
w.setup.setText("/tmp/fakeros/setup.bash")
w.button.click()

def checar():
    print("titulo:", w.windowTitle())
    print("status:", w.status.text())
    print(w.diagnostico.toPlainText())
    app.quit()

QtCore.QTimer.singleShot(4000, checar)
app.exec()
PY
```

Esperado: `T1 Debug — <seu hostname>` e `conectado a <host> (ROS humble)`.

`QT_QPA_PLATFORM=offscreen` roda o Qt sem abrir janela, o que também serve para
CI. Tire a variável se quiser ver a janela de verdade.

Vale rodar as duas variações:

- **sem** `setup.bash` no formulário → deve dar "Nenhum ambiente ROS encontrado";
- alvo `semarroba` → deve dar "Falta o usuário, use usuario@host" **sem** chamar
  o `ssh`.

---

## Tabela de erros

| O que aparece | Onde quebrou | O que fazer |
|---|---|---|
| `Falta o usuário, use usuario@host` | validação, antes do ssh | escreva `usuario@host` |
| `Autenticação recusada` | SSH | usuário ou senha errados |
| `não está no ~/.ssh/known_hosts` | SSH | rode `ssh host` uma vez e confirme a chave |
| `Não consigo resolver o host` | DNS | VPN desligada, ou nome errado |
| `Nenhum ambiente ROS encontrado` (exit 3) | bootstrap | **o SSH funcionou.** Preencha o caminho do setup.bash |
| `python3 não existe` (exit 4) | bootstrap | máquina sem python3 |
| `rclpy indisponível` | agente | sourceou o setup errado, ou falta o overlay |
| `Protocolo incompatível` | handshake | GUI e agente de versões diferentes |
| trava em "conectando…" até 45 s | qualquer ponto | veja o painel de diagnóstico |

O caso de exit 3 merece destaque porque assusta sem motivo: ele significa que
autenticação, transporte e envio do agente **funcionaram**, e só o ambiente ROS
não foi encontrado. Numa máquina que não tem ROS instalado — um nó de login de
cluster, por exemplo — é o resultado esperado, não um defeito.

---

## O que fica para depois

**Arestas conhecidas do código atual**, que valem como primeiro exercício:

1. **O exit 3 é rotulado errado.** O rodapé diz "falha na conexão" quando o SSH
   conectou perfeitamente e só o ROS não foi encontrado. Os dois estados são
   diferentes e deveriam parecer diferentes: um sinal `ros_missing` separado do
   `failed`, rodapé dizendo "SSH ok — ROS não encontrado", e o foco indo para o
   campo *Setup ROS* para você digitar o caminho e tentar de novo. É o passo 4
   do §5 do `T1_DEBUG.md`, que pede exatamente isso.
2. **A busca do bootstrap é estreita.** Só olha `/opt/ros/*/setup.bash`. Numa
   máquina onde o ROS vem por `module load`, Spack ou container, ela não acha
   nada e nem diz que essas coisas existem no host. Um relatório melhor listaria
   o que encontrou (`module`, `apptainer`, `ROS_DISTRO` já setado) e tentaria
   `import rclpy` no `python3` do sistema antes de desistir.
3. **O glob aparece cru na mensagem.** Quando `/opt/ros` não existe, o relatório
   imprime `/opt/ros/*/setup.bash` sem expandir, o que confunde. Vale dizer
   "`/opt/ros` não existe" nesse caso.

Depois disso, o `on_message()` está vazio de propósito. É onde o v0.1 pluga: transformar o
`grafo` que já chega a 1 Hz em tabela de nós e tópicos.

Antes de escrever essa parte, decida a questão em aberto do §8 do
[`T1_DEBUG.md`](T1_DEBUG.md) — lista, grafo desenhado ou painel de saúde. É a
decisão mais cara de mudar depois, porque determina a estrutura de dados que o
agente produz.
