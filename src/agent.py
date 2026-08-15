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

import json
import os
import socket
import sys
import threading
import time

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


def snapshot(node):
    """Coleta o grafo inteiro. Snapshot completo, não diff (§4)."""
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
        elif nome in ("assinar", "desassinar"):
            # Chega no v0.1, junto com a inspeção de tópico.
            emit(
                "status",
                nivel="aviso",
                msg=f"comando {nome!r} ainda não implementado",
            )
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
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()

    return 0


if __name__ == "__main__":
    sys.exit(main())
