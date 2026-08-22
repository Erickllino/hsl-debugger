#!/usr/bin/env python3
"""Agente do T1 Debug. Roda no robô, fala JSON por stdin/stdout.

Invariantes (docs/T1_DEBUG.md §3), que não são preferências:

- somente leitura: assina e lê o grafo, nunca publica, chama serviço ou escreve
  parâmetro. Não existe `create_publisher` nem `create_client` neste arquivo;
- zero dependência externa além de `rclpy`;
- arquivo único, autocontido;
- morre sozinho: EOF no `stdin` ou ociosidade encerram o processo.

`stdout` é exclusivamente o canal do protocolo. Qualquer diagnóstico vai para
`stderr` — um `print` acidental aqui corrompe a sessão inteira (§4).
"""

import collections
import importlib
import json
import os
import queue
import socket
import sys
import threading
import time

PROTOCOL_VERSION = 1
GRAPH_PERIOD = 1.0
IDLE_TIMEOUT = 600.0

# Custo de rede limitado (§3). A contagem e o Hz veem todas as mensagens; só o
# envio é decimado — a tela não consegue ler 400 Hz de qualquer jeito, e mandar
# isso pelo SSH atrapalharia o robô, que é a máquina que menos pode sofrer.
ENVIO_MAX_HZ = 10.0
JANELA_HZ = 5.0

# Truncagem de payload (§3). Uma Image 1080p tem 6 MB de `data`; sem corte, uma
# mensagem dessas engasga o canal inteiro.
#
# Os dois limites são diferentes de propósito. Um humanoide tem ~23 juntas, e
# cortar um JointState em 32 elementos truncaria justamente o caso que se quer
# ver inteiro; já um campo de bytes nunca é para ser lido, só identificado.
MAX_ITENS = 128
MAX_BYTES = 32
MAX_TEXTO = 512

# O que todo nó ROS 2 ganha de graça e não diz nada sobre o que ele faz. Sem
# descontar isso, todo nó teria tópico e serviço, e a GUI não teria como separar
# quem só existe para servir/chamar serviço de quem move o robô.
TOPICOS_PADRAO = frozenset(("/rosout", "/parameter_events"))
SERVICOS_PADRAO = frozenset((
    "describe_parameters",
    "get_parameter_types",
    "get_parameters",
    "list_parameters",
    "set_parameters",
    "set_parameters_atomically",
))

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


def nome_completo(nome, ns):
    """Junta namespace e nome num identificador único.

    Dois nós podem ter o mesmo nome curto em namespaces diferentes — comum em
    robô com várias pernas ou câmeras. O nome curto não serve como chave; a GUI
    cruza tópico↔nó por este campo.
    """
    return f"{ns.rstrip('/')}/{nome}"


def _proprios(nomes_e_tipos):
    """Tira da lista os serviços de parâmetro que o rclpy cria sozinho."""
    return [
        nome for nome, _ in nomes_e_tipos
        if nome.rsplit("/", 1)[-1] not in SERVICOS_PADRAO
    ]


def perfil(node, nome, ns):
    """Quantos tópicos e serviços *próprios* o nó tem.

    É o que permite à GUI separar nó de serviço de nó de verdade. Um cliente de
    serviço efêmero — o nó que nasce para uma chamada e morre depois — não tem
    tópico nenhum, e é ele que entra e sai do grafo a cada segundo fazendo a
    lista piscar.

    Devolve `{}` quando o nó sumiu entre a listagem e a consulta: sem
    classificação, a GUI mostra. Na dúvida, mostrar — esconder o que não
    conseguimos explicar seria a ferramenta decidindo o que pode ser visto.
    """
    try:
        pubs = node.get_publisher_names_and_types_by_node(nome, ns)
        subs = node.get_subscriber_names_and_types_by_node(nome, ns)
        servicos = node.get_service_names_and_types_by_node(nome, ns)
        clientes = node.get_client_names_and_types_by_node(nome, ns)
    except Exception as exc:
        log(f"perfil de {nome_completo(nome, ns)} indisponível: {exc}")
        return {}

    topicos = {n for n, _ in pubs} | {n for n, _ in subs}
    return {
        "topicos": len(topicos - TOPICOS_PADRAO),
        "servicos": len(_proprios(servicos)),
        "clientes": len(_proprios(clientes)),
    }


_TIPOS_LEGIVEIS = {}


def legivel(nome_tipo):
    """O tipo está no grafo — mas dá para carregar a classe dele aqui?

    Essa é a diferença entre "o tópico existe" e "eu consigo ler o tópico", e é
    exatamente o que o overlay do workspace decide (§5). Testar no snapshot faz
    a GUI mostrar a resposta na lista inteira de uma vez, em vez de o usuário
    descobrir tópico por tópico, ao clicar.

    O ambiente não muda durante a sessão, então cada tipo é testado uma vez só.
    """
    if nome_tipo not in _TIPOS_LEGIVEIS:
        try:
            carregar_tipo(nome_tipo)
            _TIPOS_LEGIVEIS[nome_tipo] = True
        except Exception:
            _TIPOS_LEGIVEIS[nome_tipo] = False
    return _TIPOS_LEGIVEIS[nome_tipo]


def snapshot(node):
    """Coleta o grafo inteiro. Snapshot completo, não diff (§4)."""
    eu = node.get_fully_qualified_name()
    nos = []
    for nome, ns in node.get_node_names_and_namespaces():
        completo = nome_completo(nome, ns)
        registro = {"nome": nome, "ns": ns, "completo": completo}
        registro.update(perfil(node, nome, ns))
        # O próprio agente não tem tópico e cairia na mesma peneira dos nós de
        # serviço. Ele fica marcado para a GUI nunca escondê-lo: "em qual
        # máquina eu estou" é informação de primeira classe (§7), e o nó do
        # agente sumindo da lista responde essa pergunta errado.
        if completo == eu:
            registro["agente"] = True
        nos.append(registro)

    topicos = []
    for nome, tipos in node.get_topic_names_and_types():
        try:
            pubs = [
                nome_completo(i.node_name, i.node_namespace)
                for i in node.get_publishers_info_by_topic(nome)
            ]
            subs = [
                nome_completo(i.node_name, i.node_namespace)
                for i in node.get_subscriptions_info_by_topic(nome)
            ]
        except Exception as exc:
            # Tópico pode sumir entre a listagem e a consulta.
            log(f"endpoints de {nome} indisponíveis: {exc}")
            pubs, subs = [], []
        topicos.append({
            "nome": nome,
            "tipos": tipos,
            "pubs": pubs,
            "subs": subs,
            "legivel": bool(tipos) and all(legivel(t) for t in tipos),
        })

    return nos, topicos


def carregar_tipo(nome_tipo):
    """"std_msgs/msg/String" → a classe Python da mensagem.

    Feito na mão em vez de `rosidl_runtime_py.utilities.get_message` para manter
    o §3: só `rclpy` e biblioteca padrão. O import é o mesmo que o ROS faria.
    """
    partes = nome_tipo.split("/")
    if len(partes) != 3:
        raise ValueError(f"nome de tipo estranho: {nome_tipo!r}")
    pacote, meio, classe = partes
    modulo = importlib.import_module(f"{pacote}.{meio}")
    return getattr(modulo, classe)


def _sequencia(valor):
    """Array de verdade, não texto. Cobre list, array.array e ndarray."""
    return hasattr(valor, "__len__") and not isinstance(
        valor, (str, bytes, bytearray)
    )


def converter(valor, estado):
    """Mensagem ROS → objeto JSON, truncando na origem.

    Truncar aqui e não depois é o ponto: converter uma nuvem de pontos inteira
    para depois jogar 99% fora gastaria a CPU do robô à toa.
    """
    if hasattr(valor, "get_fields_and_field_types"):
        return {
            campo: converter(getattr(valor, campo), estado)
            for campo in valor.get_fields_and_field_types()
        }

    if isinstance(valor, (bytes, bytearray)):
        if len(valor) > MAX_BYTES:
            estado["truncado"] = True
            return {
                "bytes": len(valor),
                "amostra_hex": valor[:MAX_BYTES].hex(),
            }
        return {"bytes": len(valor), "hex": bytes(valor).hex()}

    if isinstance(valor, str):
        if len(valor) > MAX_TEXTO:
            estado["truncado"] = True
            return valor[:MAX_TEXTO] + "…"
        return valor

    if _sequencia(valor):
        total = len(valor)
        amostra = [converter(v, estado) for v in list(valor)[:MAX_ITENS]]
        if total > MAX_ITENS:
            estado["truncado"] = True
            return {"itens": total, "amostra": amostra}
        return amostra

    if isinstance(valor, bool) or valor is None:
        return valor
    if isinstance(valor, (int, float)):
        return valor
    # Escalar do numpy (float32 e afins) não é int nem float do Python.
    if hasattr(valor, "item"):
        try:
            return valor.item()
        except Exception:
            pass
    return str(valor)


class Assinatura:
    """Um tópico aberto na tela: a subscription, a taxa e a decimação."""

    def __init__(self, topico, tipo):
        self.topico = topico
        self.tipo = tipo
        self.sub = None
        self.total = 0
        self.marcas = collections.deque()
        self.ultimo_envio = 0.0
        self.decimou = False

    def ao_receber(self, msg):
        agora = time.monotonic()
        self.total += 1

        self.marcas.append(agora)
        while self.marcas and agora - self.marcas[0] > JANELA_HZ:
            self.marcas.popleft()
        hz = None
        if len(self.marcas) >= 2:
            intervalo = self.marcas[-1] - self.marcas[0]
            if intervalo > 0:
                hz = (len(self.marcas) - 1) / intervalo

        if agora - self.ultimo_envio < 1.0 / ENVIO_MAX_HZ:
            self.decimou = True
            return
        self.ultimo_envio = agora

        estado = {"truncado": False}
        dados = converter(msg, estado)
        emit(
            "msg",
            topico=self.topico,
            tipo_msg=self.tipo,
            dados=dados,
            total=self.total,
            hz=hz,
            decimado=self.decimou,
            truncado=estado["truncado"],
        )


class Assinaturas:
    """Assinatura sob demanda (§3): só o que está aberto na tela.

    Todos os métodos rodam na thread do `spin`, nunca na thread de comandos —
    criar e destruir subscription enquanto o executor gira é receita de corrida.
    """

    def __init__(self, node):
        self.node = node
        self.ativas = {}

    def assinar(self, topico):
        if topico in self.ativas:
            return

        tipos = dict(self.node.get_topic_names_and_types()).get(topico)
        if not tipos:
            emit("status", nivel="erro", topico=topico,
                 msg=f"{topico} não está no grafo.")
            return
        if len(tipos) > 1:
            emit("status", nivel="aviso", topico=topico,
                 msg=f"{topico} tem mais de um tipo {tipos}; usando {tipos[0]}.")
        nome_tipo = tipos[0]

        try:
            classe = carregar_tipo(nome_tipo)
        except Exception as exc:
            # É aqui que a falta do overlay aparece de verdade: o tipo existe no
            # grafo mas não é importável. O §5 pede que isso seja aviso
            # explícito, não tópico silenciosamente inútil.
            emit("status", nivel="erro", topico=topico, msg=(
                f"Não consigo carregar o tipo {nome_tipo}: {exc}. "
                "Normalmente é o overlay do workspace não carregado — informe o "
                "setup.bash do workspace no campo Setup ROS."
            ))
            return

        assinatura = Assinatura(topico, nome_tipo)
        try:
            assinatura.sub = self.node.create_subscription(
                classe, topico, assinatura.ao_receber, self._qos(topico)
            )
        except Exception as exc:
            emit("status", nivel="erro", topico=topico,
                 msg=f"Não consegui assinar {topico}: {exc}")
            return

        self.ativas[topico] = assinatura
        log(f"assinado {topico} ({nome_tipo})")

    def desassinar(self, topico):
        assinatura = self.ativas.pop(topico, None)
        if assinatura is None:
            return
        if assinatura.sub is not None:
            self.node.destroy_subscription(assinatura.sub)
        log(f"desassinado {topico}")

    def desassinar_tudo(self):
        for topico in list(self.ativas):
            self.desassinar(topico)

    def _qos(self, topico):
        """QoS casado com o publisher (§3).

        Sem isso, tópico de sensor (`BEST_EFFORT`) nunca entrega nada e o
        usuário conclui que a ferramenta está quebrada. A regra do DDS é que o
        leitor não pode pedir mais do que o escritor oferece; quando os
        publishers discordam entre si, cair no mais permissivo é o que faz o
        leitor casar com todos.
        """
        from rclpy.qos import (
            DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy,
        )

        perfil = QoSProfile(depth=10, history=HistoryPolicy.KEEP_LAST)

        try:
            infos = self.node.get_publishers_info_by_topic(topico)
        except Exception as exc:
            log(f"QoS de {topico} indisponível ({exc}); usando o padrão")
            return perfil
        if not infos:
            return perfil

        confs = {i.qos_profile.reliability for i in infos}
        durs = {i.qos_profile.durability for i in infos}

        if confs == {ReliabilityPolicy.RELIABLE}:
            perfil.reliability = ReliabilityPolicy.RELIABLE
        else:
            perfil.reliability = ReliabilityPolicy.BEST_EFFORT

        if durs == {DurabilityPolicy.TRANSIENT_LOCAL}:
            perfil.durability = DurabilityPolicy.TRANSIENT_LOCAL
        else:
            perfil.durability = DurabilityPolicy.VOLATILE

        return perfil


class Commands(threading.Thread):
    """Lê comandos da GUI. EOF no stdin é o sinal de encerramento."""

    daemon = True

    def __init__(self):
        super().__init__()
        self.parar = threading.Event()
        self.ultimo = time.monotonic()
        # Assinar mexe no nó, e o nó é da thread do spin. Esta thread só
        # enfileira; quem executa é o laço principal.
        self.fila = queue.Queue()

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
        elif nome in ("assinar", "desassinar"):
            topico = cmd.get("topico")
            if not topico:
                emit("status", nivel="aviso", msg=f"{nome} sem tópico")
                return
            self.fila.put((nome, topico))
        else:
            emit("status", nivel="aviso", msg=f"comando desconhecido: {nome!r}")


def main():
    try:
        import rclpy
    except ImportError as exc:
        # Ambiente ROS carregado mas incompleto: a GUI mostra isso como erro de
        # ambiente, não como falha de conexão (§5).
        emit("status", nivel="erro", msg=f"rclpy indisponível: {exc}")
        return 3

    rclpy.init(args=[])
    node = rclpy.create_node("t1_debug_agent")

    emit(
        "hello",
        ros_distro=os.environ.get("ROS_DISTRO", "desconhecido"),
        host=socket.gethostname(),
        pid=os.getpid(),
    )
    log(f"agente ativo, protocolo v{PROTOCOL_VERSION}")

    comandos = Commands()
    comandos.start()
    assinaturas = Assinaturas(node)

    proximo_grafo = 0.0
    try:
        while not comandos.parar.is_set():
            rclpy.spin_once(node, timeout_sec=0.1)

            while True:
                try:
                    acao, topico = comandos.fila.get_nowait()
                except queue.Empty:
                    break
                if acao == "assinar":
                    assinaturas.assinar(topico)
                else:
                    assinaturas.desassinar(topico)

            agora = time.monotonic()
            if agora >= proximo_grafo:
                nos, topicos = snapshot(node)
                emit("grafo", nos=nos, topicos=topicos)
                proximo_grafo = agora + GRAPH_PERIOD

            if agora - comandos.ultimo > IDLE_TIMEOUT:
                log(f"ocioso por {IDLE_TIMEOUT:.0f}s, encerrando")
                break
    except KeyboardInterrupt:
        pass
    finally:
        assinaturas.desassinar_tudo()
        node.destroy_node()
        rclpy.shutdown()

    return 0


if __name__ == "__main__":
    sys.exit(main())
