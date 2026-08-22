"""A tela que aparece depois de conectar: nós, tópicos e inspeção.

Decisão do §8 do `docs/T1_DEBUG.md`: **lista** primeiro, e o grafo desenhado
como segunda leitura da mesma estrutura de dados — é a aba *Desenho*, em
`desenho.py`. Nenhum dado novo foi pedido ao agente para ela existir: as arestas
saem dos mesmos `pubs`/`subs` cruzados por nome completo de nó.

Três coisas neste arquivo não são estilo, são consequência do protocolo:

- o `grafo` chega a 1 Hz com o snapshot inteiro (§4). Reconstruir as tabelas a
  cada segundo perderia seleção e faria scroll pular, então só reconstruímos
  quando o snapshot muda de verdade;
- assinatura é sob demanda (§3): só o tópico aberto na tela fica assinado, e
  trocar de tópico desassina o anterior. Ninguém paga banda por tópico fechado;
- a página não fala SSH. Ela emite `assinar`/`desassinar` e recebe dados por
  método; quem conversa com a sessão é o `menu.py`.
"""

import json

from PySide6 import QtCore, QtGui, QtWidgets

from src.desenho import PainelDesenho

SEM_TIPO = "(tipo desconhecido)"


def _resumo_tipos(tipos):
    if not tipos:
        return SEM_TIPO
    return ", ".join(tipos)


def _celula_tipo(topico):
    """Célula da coluna Tipo: texto e, quando for o caso, o aviso que a pinta.

    São dois problemas diferentes e a distinção importa. *Sem tipo no grafo* é o
    DDS não ter dito o tipo. *Tipo que não importa* é o tipo estar lá e o
    ambiente carregado não ter o pacote da mensagem — o caso das mensagens do
    SDK, que se resolve acrescentando o setup.bash do workspace (§5). O agente
    responde a segunda em `legivel`; tópico de agente antigo, sem o campo, é
    tratado como legível.
    """
    if not topico["tipos"]:
        return (SEM_TIPO, "Sem tipo no grafo. Costuma ser overlay do workspace "
                          "não carregado (§5).")
    texto = _resumo_tipos(topico["tipos"])
    if topico.get("legivel", True):
        return texto
    return (texto, "O tipo está no grafo, mas o pacote da mensagem não existe no "
                   "ambiente carregado — não dá para assinar este tópico. "
                   "Acrescente o setup.bash do workspace no campo Setup ROS (§5).")


class GraphPage(QtWidgets.QWidget):
    """Lista o grafo e inspeciona um tópico por vez."""

    assinar = QtCore.Signal(str)
    desassinar = QtCore.Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)

        self._bruto = ([], [])       # último snapshot cru, para refiltrar
        self._nos = []
        self._topicos = []
        self._servicos = []          # nós escondidos pelo filtro de serviço
        self._assinatura = None      # tópico assinado agora, ou None
        self._chave = None           # último snapshot, para detectar mudança
        self._no_filtro = None       # nó escolhido, filtra a tabela de tópicos

        self.busca = QtWidgets.QLineEdit()
        self.busca.setPlaceholderText("filtrar por nome…")
        self.busca.setClearButtonEnabled(True)

        # Nó de serviço entra e sai do grafo a cada chamada, e a lista pisca
        # junto. Esconder é o padrão porque o que pisca atrapalha ler o que não
        # pisca — mas é caixa marcável, à vista, e a contagem ao lado sempre diz
        # quantos estão escondidos, com os nomes no tooltip. Escondido não é
        # sumido.
        self.sem_servicos = QtWidgets.QCheckBox("ocultar serviços")
        self.sem_servicos.setChecked(True)
        self.sem_servicos.setToolTip(
            "Esconde os nós que não publicam nem assinam tópico nenhum — fora "
            "/rosout e /parameter_events, que todo nó tem. Na prática são "
            "servidores e clientes de serviço; o cliente efêmero nasce a cada "
            "chamada e some depois, e é ele que faz a lista piscar."
        )

        self.contagem = QtWidgets.QLabel("—")
        self.contagem.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)

        topo = QtWidgets.QHBoxLayout()
        topo.addWidget(self.busca, stretch=1)
        topo.addWidget(self.sem_servicos)
        topo.addWidget(self.contagem)

        self.tabela_nos = self._tabela(["Nó", "Publica", "Assina"])
        self.tabela_topicos = self._tabela(["Tópico", "Tipo", "Pub", "Sub"])

        self.aviso_filtro = QtWidgets.QLabel()
        self.aviso_filtro.setVisible(False)
        self.aviso_filtro.setTextFormat(QtCore.Qt.RichText)
        self.aviso_filtro.linkActivated.connect(self._limpar_filtro_no)

        col_nos = self._coluna("Nós", self.tabela_nos)
        col_topicos = self._coluna("Tópicos", self.tabela_topicos, self.aviso_filtro)

        listas = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        listas.addWidget(col_nos)
        listas.addWidget(col_topicos)
        listas.setStretchFactor(0, 3)
        listas.setStretchFactor(1, 5)

        # Aba, não painel lado a lado: o desenho quer a largura inteira, e a
        # pergunta "quem fala com quem" não costuma ser feita ao mesmo tempo que
        # "quais tópicos existem". O inspetor fica embaixo das duas, porque ele
        # responde à mesma seleção venha ela de onde vier.
        self.painel_desenho = PainelDesenho()
        self.desenho = self.painel_desenho.desenho

        self.abas = QtWidgets.QTabWidget()
        self.abas.addTab(listas, "Listas")
        self.abas.addTab(self.painel_desenho, "Desenho")

        self.inspetor = Inspector()

        divisor = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        divisor.addWidget(self.abas)
        divisor.addWidget(self.inspetor)
        divisor.setStretchFactor(0, 3)
        divisor.setStretchFactor(1, 2)

        fora = QtWidgets.QVBoxLayout(self)
        fora.addLayout(topo)
        fora.addWidget(divisor, stretch=1)

        self.busca.textChanged.connect(self._aplicar_filtro)
        self.busca.textChanged.connect(self.desenho.definir_filtro)
        self.sem_servicos.toggled.connect(self._refiltrar)
        self.tabela_nos.itemSelectionChanged.connect(self._no_selecionado)
        self.tabela_topicos.itemSelectionChanged.connect(self._topico_selecionado)

        # Clicar no desenho é clicar na tabela: a seleção é uma só, e é a tabela
        # que manda. Assim o caminho que assina tópico continua sendo um único
        # (`_topico_selecionado`) em vez de dois que precisam concordar.
        self.desenho.escolheu_no.connect(
            lambda nome: self._selecionar(self.tabela_nos, nome, avisar=True)
        )
        self.desenho.escolheu_topico.connect(
            lambda nome: self._selecionar(self.tabela_topicos, nome, avisar=True)
        )

    # -- construção -------------------------------------------------------

    def _tabela(self, colunas):
        t = QtWidgets.QTableWidget(0, len(colunas))
        t.setHorizontalHeaderLabels(colunas)
        t.setEditTriggers(QtWidgets.QTableWidget.NoEditTriggers)
        t.setSelectionBehavior(QtWidgets.QTableWidget.SelectRows)
        t.setSelectionMode(QtWidgets.QTableWidget.SingleSelection)
        t.setAlternatingRowColors(True)
        t.verticalHeader().setVisible(False)
        t.horizontalHeader().setStretchLastSection(False)
        t.horizontalHeader().setSortIndicatorShown(True)
        # A ordem padrão precisa ser explícita: ligar a ordenação sem indicador
        # definido deixa a tabela em ordem decrescente, que ninguém pediu.
        t.sortByColumn(0, QtCore.Qt.AscendingOrder)
        t.setSortingEnabled(True)
        t._ajustada = False
        return t

    def _coluna(self, titulo, tabela, extra=None):
        rotulo = QtWidgets.QLabel(f"<b>{titulo}</b>")
        caixa = QtWidgets.QVBoxLayout()
        caixa.setContentsMargins(0, 0, 0, 0)
        caixa.addWidget(rotulo)
        if extra is not None:
            caixa.addWidget(extra)
        caixa.addWidget(tabela, stretch=1)
        w = QtWidgets.QWidget()
        w.setLayout(caixa)
        return w

    # -- entrada de dados -------------------------------------------------

    def atualizar(self, nos, topicos):
        """Recebe um snapshot do agente. Chamado a ~1 Hz."""
        self._bruto = (nos, topicos)
        nos, topicos, self._servicos = self._separar_servicos(nos, topicos)

        # A separação vem antes da chave de propósito: nó de serviço que aparece
        # e some a cada segundo mudaria a chave toda vez e reconstruiria as duas
        # tabelas à toa. Filtrar primeiro é o que faz a tela parar de piscar.
        chave = self._resumir(nos, topicos)
        if chave == self._chave:
            # Nada mudou no grafo. Redesenhar aqui só faria a seleção piscar.
            return
        self._chave = chave

        self._nos = sorted(nos, key=lambda n: n.get("completo", n["nome"]))
        self._topicos = sorted(topicos, key=lambda t: t["nome"])

        self._preencher_nos()
        self._preencher_topicos()
        self._aplicar_filtro()
        # O desenho precisa saber quem a lista escondeu: um nó de serviço não
        # tem tópico próprio, mas publica em /rosout como todo mundo, e a aresta
        # do /rosout o traria de volta para a tela.
        self.desenho.definir_servicos(self._servicos)
        self.desenho.atualizar(self._nos, self._topicos)

        # Um tópico assinado pode ter sumido do grafo — o publisher morreu. Isso
        # é informação, não erro: o inspetor continua mostrando a última
        # mensagem, mas avisa que o tópico não existe mais.
        if self._assinatura is not None:
            vivos = {t["nome"] for t in self._topicos}
            self.inspetor.marcar_sumido(self._assinatura not in vivos)

    def _separar_servicos(self, nos, topicos):
        """(nós a mostrar, tópicos sem os nós escondidos, nomes escondidos).

        Quem classifica é o agente, que pergunta ao grafo do robô quantos
        tópicos próprios cada nó tem (`src/agent.py`, `perfil`). Nó que ele não
        conseguiu classificar vem sem o campo `topicos` e fica visível: na
        dúvida, mostrar.

        Os tópicos também são reescritos, e isso não é excesso de zelo. Nó de
        serviço não tem tópico *próprio*, mas publica em /rosout como qualquer
        um: deixar o nome dele nos `pubs` faria a contagem de /rosout oscilar a
        cada aparição e reconstruiria as tabelas do mesmo jeito — o pisca que a
        caixa existe para tirar.
        """
        if not self.sem_servicos.isChecked():
            return list(nos), list(topicos), []

        visiveis, servicos = [], set()
        for n in nos:
            if n.get("agente"):
                # O nó do próprio agente também não tem tópico, mas escondê-lo
                # tiraria da tela a prova de que a sessão está viva no robô.
                visiveis.append(n)
            elif n.get("topicos") == 0:
                servicos.add(n.get("completo", n["nome"]))
            else:
                visiveis.append(n)

        if servicos:
            topicos = [
                dict(t,
                     pubs=[p for p in t["pubs"] if p not in servicos],
                     subs=[s for s in t["subs"] if s not in servicos])
                for t in topicos
            ]
        return visiveis, list(topicos), sorted(servicos)

    @QtCore.Slot()
    def _refiltrar(self):
        """A caixa mudou: o snapshot é o mesmo, o que a tela mostra não é."""
        self._chave = None
        self.atualizar(*self._bruto)

    def _resumir(self, nos, topicos):
        """Reduz o snapshot ao que a tela mostra, para comparar dois snapshots.

        Sem isso a comparação pegaria diferenças que não aparecem na tela e a
        tabela seria reconstruída à toa.
        """
        return (
            tuple(sorted(n.get("completo", n["nome"]) for n in nos)),
            tuple(sorted(
                (t["nome"], tuple(t["tipos"]), tuple(sorted(t["pubs"])),
                 tuple(sorted(t["subs"])))
                for t in topicos
            )),
        )

    def _preencher_nos(self):
        # Quantos tópicos cada nó publica e assina. É a pergunta que se faz
        # olhando para uma lista de nós: "esse aí está fazendo alguma coisa?"
        publica = {}
        assina = {}
        for t in self._topicos:
            for p in t["pubs"]:
                publica[p] = publica.get(p, 0) + 1
            for s in t["subs"]:
                assina[s] = assina.get(s, 0) + 1

        linhas = []
        for no in self._nos:
            completo = no.get("completo", no["nome"])
            linhas.append((completo, publica.get(completo, 0), assina.get(completo, 0)))

        self._preencher(self.tabela_nos, linhas, chave=lambda l: l[0])

    def _preencher_topicos(self):
        linhas = []
        for t in self._topicos:
            linhas.append((
                t["nome"],
                _celula_tipo(t),
                len(t["pubs"]),
                len(t["subs"]),
            ))
        self._preencher(self.tabela_topicos, linhas, chave=lambda l: l[0])

    def _preencher(self, tabela, linhas, chave):
        """Reconstrói uma tabela preservando seleção e scroll."""
        selecionado = self._selecao(tabela)
        scroll = tabela.verticalScrollBar().value()

        # Com ordenação ligada, inserir linha por linha reordena no meio do
        # preenchimento e embaralha as células. Desliga, preenche, religa.
        tabela.setSortingEnabled(False)
        tabela.setRowCount(len(linhas))
        for i, linha in enumerate(linhas):
            for j, valor in enumerate(linha):
                # Uma célula pode vir como (texto, aviso): o aviso pinta e vira
                # tooltip. É como o tópico que a GUI não consegue ler diz isso na
                # própria linha, em vez de só quando você clica nele.
                aviso = None
                if isinstance(valor, tuple):
                    valor, aviso = valor

                item = QtWidgets.QTableWidgetItem()
                if isinstance(valor, int):
                    # Guardar o int como dado faz a ordenação ser numérica; como
                    # texto, 10 viria antes de 2.
                    item.setData(QtCore.Qt.DisplayRole, valor)
                    item.setTextAlignment(QtCore.Qt.AlignCenter)
                else:
                    item.setText(valor)
                    if aviso:
                        item.setForeground(QtGui.QBrush(QtGui.QColor("#b06000")))
                        item.setToolTip(aviso)
                tabela.setItem(i, j, item)
        tabela.setSortingEnabled(True)

        # Só na primeira vez: depois disso a largura é do usuário, e reajustar a
        # cada mudança do grafo desfaria o que ele arrastou.
        if not tabela._ajustada and linhas:
            tabela.resizeColumnsToContents()
            tabela._ajustada = True

        if selecionado is not None:
            self._selecionar(tabela, selecionado)
        tabela.verticalScrollBar().setValue(scroll)

    # -- seleção e filtro -------------------------------------------------

    def _selecao(self, tabela):
        linhas = tabela.selectionModel().selectedRows()
        if not linhas:
            return None
        item = tabela.item(linhas[0].row(), 0)
        return item.text() if item else None

    def _selecionar(self, tabela, texto, avisar=False):
        """Seleciona a linha pelo nome.

        `avisar=False` é o caso de reconstruir a tabela: a seleção é a mesma de
        antes, e deixar o sinal passar dispararia assinatura de tópico a cada
        snapshot. `avisar=True` é o clique vindo do desenho, que **precisa**
        percorrer o mesmo caminho de um clique na tabela.
        """
        for i in range(tabela.rowCount()):
            item = tabela.item(i, 0)
            if item is not None and item.text() == texto:
                tabela.blockSignals(not avisar)
                tabela.setRowHidden(i, False)   # clique no desenho vence o filtro
                tabela.selectRow(i)
                tabela.scrollToItem(item)
                tabela.blockSignals(False)
                return

    @QtCore.Slot()
    def _no_selecionado(self):
        self._no_filtro = self._selecao(self.tabela_nos)
        self._aplicar_filtro()
        self.desenho.destacar("no", self._no_filtro)

    @QtCore.Slot()
    def _limpar_filtro_no(self):
        self.tabela_nos.clearSelection()
        self._no_filtro = None
        self._aplicar_filtro()

    @QtCore.Slot()
    def _aplicar_filtro(self):
        termo = self.busca.text().strip().lower()

        visiveis_nos = 0
        for i in range(self.tabela_nos.rowCount()):
            nome = self.tabela_nos.item(i, 0).text()
            mostrar = termo in nome.lower()
            self.tabela_nos.setRowHidden(i, not mostrar)
            visiveis_nos += mostrar

        # Filtro por nó: só tópicos em que ele aparece como pub ou sub.
        do_no = None
        if self._no_filtro:
            do_no = {
                t["nome"] for t in self._topicos
                if self._no_filtro in t["pubs"] or self._no_filtro in t["subs"]
            }

        visiveis_top = 0
        for i in range(self.tabela_topicos.rowCount()):
            nome = self.tabela_topicos.item(i, 0).text()
            mostrar = termo in nome.lower()
            if do_no is not None:
                mostrar = mostrar and nome in do_no
            self.tabela_topicos.setRowHidden(i, not mostrar)
            visiveis_top += mostrar

        if do_no is None:
            self.aviso_filtro.setVisible(False)
        else:
            self.aviso_filtro.setVisible(True)
            self.aviso_filtro.setText(
                f"só de <b>{self._no_filtro}</b> — <a href='#'>mostrar todos</a>"
            )

        partes = [
            f"{visiveis_nos}/{len(self._nos)} nós",
            f"{visiveis_top}/{len(self._topicos)} tópicos",
        ]
        dica = []
        if self._servicos:
            partes.append(f"{len(self._servicos)} serviços ocultos")
            dica.append("escondidos pela caixa 'ocultar serviços':\n" +
                        "\n".join(self._servicos))

        # Tipo que está no grafo mas não importa no ambiente carregado é a
        # resposta à pergunta "meu setup pegou as mensagens do SDK?". Mostrar o
        # total aqui responde na hora, sem precisar clicar tópico por tópico.
        sem_overlay = [t["nome"] for t in self._topicos
                       if t["tipos"] and not t.get("legivel", True)]
        if sem_overlay:
            partes.append(f"{len(sem_overlay)} sem tipo carregado")
            dica.append("tipo no grafo mas não importável — falta o setup.bash "
                        "do workspace:\n" + "\n".join(sem_overlay))

        self.contagem.setText(" · ".join(partes))
        self.contagem.setToolTip("\n\n".join(dica))

    # -- inspeção ---------------------------------------------------------

    @QtCore.Slot()
    def _topico_selecionado(self):
        nome = self._selecao(self.tabela_topicos)
        if nome == self._assinatura:
            return

        # Trocar de tópico desassina o anterior: o §3 diz "só o que está aberto
        # na tela". Sem isso, um passeio pela lista deixaria dez assinaturas
        # ativas consumindo banda do robô.
        if self._assinatura is not None:
            self.desassinar.emit(self._assinatura)
        self._assinatura = nome

        self.desenho.destacar("topico", nome)
        # Fio parado até a primeira medida chegar: velocidade só vem de Hz
        # medido, nunca de chute.
        self.desenho.fluxo(nome, None)

        if nome is None:
            self.inspetor.limpar()
            return

        dados = next((t for t in self._topicos if t["nome"] == nome), None)
        self.inspetor.abrir(nome, dados)
        self.assinar.emit(nome)

    def receber_msg(self, msg):
        if msg.get("topico") == self._assinatura:
            self.inspetor.nova_msg(msg)
            # O Hz conta todas as mensagens, não as que sobraram da decimação
            # (§3) — então a animação anda na taxa real do robô, e não na taxa
            # que coube no SSH.
            self.desenho.fluxo(msg["topico"], msg.get("hz"))

    def receber_status(self, msg):
        """Erro do agente sobre o tópico aberto (tipo sem overlay, QoS, …)."""
        if msg.get("topico") == self._assinatura:
            self.inspetor.problema(msg.get("msg", "erro no agente"))

    def encerrar(self):
        """Sessão caindo: nada a desassinar, o agente morre junto."""
        self._assinatura = None
        self._chave = None
        self._bruto = ([], [])
        self._servicos = []
        self.inspetor.limpar()
        self.desenho.limpar()


class Inspector(QtWidgets.QWidget):
    """O que sai de um tópico: metadados, taxa e a última mensagem."""

    def __init__(self, parent=None):
        super().__init__(parent)

        self._nome = None
        self.titulo = QtWidgets.QLabel("selecione um tópico")
        fonte = self.titulo.font()
        fonte.setBold(True)
        self.titulo.setFont(fonte)

        self.meta = QtWidgets.QLabel("")
        self.meta.setWordWrap(True)
        self.meta.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)

        self.stats = QtWidgets.QLabel("")

        self.congelar = QtWidgets.QCheckBox("congelar")
        self.congelar.setToolTip(
            "Para de atualizar a exibição para você conseguir ler. A assinatura "
            "continua ativa e a taxa continua contando."
        )

        self.corpo = QtWidgets.QPlainTextEdit()
        self.corpo.setReadOnly(True)
        self.corpo.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
        self.corpo.setFont(QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont))

        cabecalho = QtWidgets.QHBoxLayout()
        cabecalho.addWidget(self.titulo)
        cabecalho.addStretch()
        cabecalho.addWidget(self.stats)
        cabecalho.addWidget(self.congelar)

        fora = QtWidgets.QVBoxLayout(self)
        fora.setContentsMargins(0, 6, 0, 0)
        fora.addLayout(cabecalho)
        fora.addWidget(self.meta)
        fora.addWidget(self.corpo, stretch=1)

    def limpar(self):
        self._nome = None
        self.titulo.setText("selecione um tópico")
        self.meta.setText("")
        self.stats.setText("")
        self.corpo.clear()

    def abrir(self, nome, dados):
        self._nome = nome
        self.titulo.setText(nome)
        self.stats.setText("aguardando mensagem…")
        self.corpo.clear()
        if not dados:
            self.meta.setText("")
            return

        pubs = ", ".join(dados["pubs"]) or "ninguém"
        subs = ", ".join(dados["subs"]) or "ninguém"
        self.meta.setText(
            f"tipo: {_resumo_tipos(dados['tipos'])}<br>"
            f"publicam: {pubs}<br>"
            f"assinam: {subs}"
        )

    def marcar_sumido(self, sumido):
        """O publisher morreu enquanto o tópico estava aberto — ou voltou.

        Reconstruir o título a partir do nome guardado, em vez de anexar texto,
        é o que faz o aviso saber ir embora quando o nó volta.
        """
        if self._nome is None:
            return
        if sumido:
            self.titulo.setText(f"{self._nome} — sumiu do grafo")
        else:
            self.titulo.setText(self._nome)

    def problema(self, texto):
        self.stats.setText("erro")
        self.corpo.setPlainText(texto)

    def nova_msg(self, msg):
        hz = msg.get("hz")
        total = msg.get("total", 0)
        partes = []
        if hz is not None:
            partes.append(f"{hz:.1f} Hz")
        partes.append(f"{total} msgs")
        if msg.get("decimado"):
            # Honestidade sobre o que a tela mostra: o §3 manda decimar o envio,
            # não a contagem. Sem esse aviso alguém conclui que perdeu mensagem.
            partes.append("exibição decimada")
        self.stats.setText(" · ".join(partes))

        if self.congelar.isChecked():
            return

        dados = msg.get("dados")
        texto = json.dumps(dados, ensure_ascii=False, indent=2)
        if msg.get("truncado"):
            texto += "\n\n… payload truncado pelo agente (§3)."

        # setPlainText em vez de append: o que interessa é a mensagem atual, e um
        # tópico a 10 Hz encheria a tela de histórico ilegível em segundos.
        barra = self.corpo.verticalScrollBar().value()
        self.corpo.setPlainText(texto)
        self.corpo.verticalScrollBar().setValue(barra)
