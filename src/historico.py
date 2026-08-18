"""Endereços já usados, para não digitar IP toda vez.

O §7 do `docs/T1_DEBUG.md` trata isso como parte da interface, não como conforto:
"se a pessoa precisa descobrir e digitar IP toda vez, o atrito que estamos
evitando volta multiplicado pelo tamanho do time".

**Este módulo não conhece senha.** Não é uma regra de estilo, é o §6: basta
existir um lugar que guarda senha para que, em poucos meses, haja senha de robô
em texto plano no disco de várias pessoas — inclusive de robô que não é do time.
A função `registrar` nem recebe o campo de senha, e a leitura descarta qualquer
chave que não seja esperada. Se alguém um dia acrescentar `"senha"` ao arquivo à
mão, o carregamento joga fora.

Só entra no histórico endereço que **conectou**. Salvar tentativa fracassada
encheria a lista dos seus próprios erros de digitação.
"""

import json
import os
from datetime import datetime
from pathlib import Path

from PySide6 import QtCore

MAX_ENTRADAS = 12

# O que pode existir num registro. Whitelist, não blacklist: o que não está
# aqui é descartado na leitura, incluindo o que ninguém previu.
CAMPOS = ("alvo", "setup", "ultimo")


def caminho_padrao():
    base = QtCore.QStandardPaths.writableLocation(
        QtCore.QStandardPaths.GenericConfigLocation
    )
    raiz = Path(base) if base else Path.home() / ".config"
    return raiz / "t1-debug" / "hosts.json"


class Historico:
    """Lista de endereços, do mais recente para o mais antigo."""

    def __init__(self, arquivo=None):
        self.arquivo = Path(arquivo) if arquivo else caminho_padrao()
        self.entradas = self._ler()

    # -- leitura ----------------------------------------------------------

    def _ler(self):
        try:
            bruto = json.loads(self.arquivo.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except (ValueError, OSError, UnicodeDecodeError):
            # Arquivo corrompido não pode impedir a ferramenta de abrir: o
            # histórico é conveniência, e a próxima gravação o conserta.
            return []

        if not isinstance(bruto, list):
            return []

        entradas = []
        vistos = set()
        for item in bruto:
            if not isinstance(item, dict):
                continue
            alvo = item.get("alvo")
            if not isinstance(alvo, str) or not alvo.strip():
                continue
            alvo = alvo.strip()
            if alvo in vistos:
                continue
            vistos.add(alvo)
            entradas.append({
                "alvo": alvo,
                "setup": str(item.get("setup") or ""),
                "ultimo": str(item.get("ultimo") or ""),
            })
        return entradas[:MAX_ENTRADAS]

    # -- consulta ---------------------------------------------------------

    def alvos(self):
        return [e["alvo"] for e in self.entradas]

    def entrada(self, alvo):
        alvo = (alvo or "").strip()
        return next((e for e in self.entradas if e["alvo"] == alvo), None)

    def setup_de(self, alvo):
        e = self.entrada(alvo)
        return e["setup"] if e else ""

    def mais_recente(self):
        return self.entradas[0] if self.entradas else None

    # -- escrita ----------------------------------------------------------

    def registrar(self, alvo, setup=""):
        """Sobe o endereço para o topo. Devolve mensagem de erro, ou None."""
        alvo = (alvo or "").strip()
        if not alvo:
            return None

        self.entradas = [e for e in self.entradas if e["alvo"] != alvo]
        self.entradas.insert(0, {
            "alvo": alvo,
            "setup": (setup or "").strip(),
            "ultimo": datetime.now().isoformat(timespec="seconds"),
        })
        del self.entradas[MAX_ENTRADAS:]
        return self._gravar()

    def esquecer(self, alvo):
        alvo = (alvo or "").strip()
        antes = len(self.entradas)
        self.entradas = [e for e in self.entradas if e["alvo"] != alvo]
        if len(self.entradas) == antes:
            return None
        return self._gravar()

    def _gravar(self):
        texto = json.dumps(
            [{c: e[c] for c in CAMPOS} for e in self.entradas],
            ensure_ascii=False,
            indent=2,
        )
        temporario = self.arquivo.with_suffix(".tmp")
        try:
            self.arquivo.parent.mkdir(parents=True, exist_ok=True)
            # Grava em arquivo temporário e troca: um Ctrl-C no meio da escrita
            # deixaria o histórico truncado se fosse escrito por cima.
            temporario.write_text(texto, encoding="utf-8")
            os.replace(temporario, self.arquivo)
        except OSError as exc:
            return f"não consegui salvar o histórico em {self.arquivo}: {exc}"
        return None
