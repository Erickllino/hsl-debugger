"""O grafo desenhado: os mesmos dados da lista, lidos como arestas.

O §8 do `docs/T1_DEBUG.md` decidiu lista primeiro e deixou o desenho para depois,
"como uma segunda leitura da mesma estrutura de dados". É isto aqui, e por isso
`agent.py` não ganha uma linha: `nos`, `pubs` e `subs` já chegam com nome
completo (`ns` + nome), que é a chave que uma aresta precisa.

**Por que não `rqt_graph`.** Ele existe e faz exatamente isto, mas rodando na
máquina onde o DDS está — o robô — e exportando a janela por `ssh -X`. Três
coisas quebram nesse caminho: `rqt` vem no `ros-*-desktop` e robô costuma ter
`ros-base`; instalar num robô emprestado é o que o §2.2 proíbe; e X11 sobre wifi
é lento. O próprio container de `demo/` mostra o sintoma: tem `X11Forwarding
yes` e **não** tem `xauth`, então o encaminhamento falha calado.

Três decisões deste arquivo que não são estilo:

- o grafo é **bipartido**: nó → tópico → nó. Ligar publicador direto no assinante
  esconderia por qual tópico eles conversam, que é justamente a pergunta;
- o desenho começa mostrando **tudo**, e qualquer caixa se apaga com
  **ctrl+clique** (o botão *reorganizar* devolve). `/rosout` e
  `/parameter_events` costumam ser os dois primeiros a apagar, porque todo nó
  publica neles e o desenho vira novelo — mas essa é a sua decisão, tomada
  vendo o grafo, e não um filtro que a ferramenta aplica antes de te mostrar
  qualquer coisa;
- ciclo é normal aqui (controlador → comando → planta → estado → controlador),
  então o layout **não pode supor DAG**. As arestas de retorno são detectadas,
  ignoradas no cálculo das camadas e desenhadas tracejadas.

Sobre a animação de pacotes: ela roda **só no tópico aberto**, porque Hz só
existe para o tópico assinado. ROS 2 não informa taxa de publicação sem assinar,
e assinar tudo para poder animar tudo violaria o §3 ("assinatura sob demanda").
Fio parado quer dizer "não estou medindo", nunca "não está passando nada" — e
essa diferença precisa ficar clara na tela, senão a animação vira mentira.
"""

import math

from PySide6 import QtCore, QtGui, QtWidgets

# Nada começa escondido. O desenho mostra o grafo que o robô tem, e quem decide
# o que atrapalha é quem está olhando — ctrl+clique apaga, *reorganizar* devolve.
# `/rosout` e `/parameter_events` costumam ser os dois primeiros a apagar (todo
# nó publica neles, e por isso ligam todo mundo em todo mundo), mas escondê-los
# por padrão seria a ferramenta decidindo por você o que você pode ver.

# Acima disso o desenho vira novelo e o layout começa a custar caro a cada
# mudança do grafo (que chega a 1 Hz). Melhor dizer isso do que entregar borrão.
LIMITE = 300

LARGURA_MAX = 200
ALTURA_NO = 28
ESPACO_X = 300          # LARGURA_MAX + folga: a seta precisa caber no meio
ESPACO_Y = 38


# -- o grafo, sem Qt nenhum -----------------------------------------------
#
# Estas três funções são Python puro de propósito: dá para testá-las sem abrir
# janela, e é o que o checkpoint faz.


def montar(nos, topicos, termo="", ocultos=()):
    """Snapshot do agente → (vertices, arestas).

    Vértice é `("no", nome)` ou `("topico", nome)`. O prefixo não é enfeite: um
    nó `/chatter` e um tópico `/chatter` podem coexistir, e sem ele os dois
    viravam a mesma caixa.
    """
    vertices = {}
    arestas = []
    ocultos = set(ocultos)

    for n in nos:
        chave = ("no", n.get("completo", n["nome"]))
        if chave not in ocultos:
            vertices[chave] = None

    for t in topicos:
        nome = t["nome"]
        if ("topico", nome) in ocultos:
            continue
        vertices[("topico", nome)] = None
        for p in t["pubs"]:
            # Um publicador pode aparecer aqui sem estar em `nos`: o snapshot é
            # tirado em duas chamadas e o grafo muda no meio. Melhor desenhar o
            # nó que a aresta cita do que a aresta sair do nada.
            if ("no", p) in ocultos:
                continue
            vertices.setdefault(("no", p), None)
            arestas.append((("no", p), ("topico", nome)))
        for s in t["subs"]:
            if ("no", s) in ocultos:
                continue
            vertices.setdefault(("no", s), None)
            arestas.append((("topico", nome), ("no", s)))

    if termo:
        vertices, arestas = _vizinhanca(vertices, arestas, termo.strip().lower())

    return list(vertices), arestas


def _vizinhanca(vertices, arestas, termo):
    """Filtro: o que casa **mais os vizinhos diretos**.

    Guardar só o que casa deixaria caixas soltas sem aresta nenhuma — filtrar
    por `imu` mostraria o tópico e esconderia quem publica nele, que é a metade
    interessante da resposta.
    """
    casam = {v for v in vertices if termo in v[1].lower()}
    if not casam:
        return {}, []

    manter = set(casam)
    for a, b in arestas:
        if a in casam:
            manter.add(b)
        if b in casam:
            manter.add(a)

    return (
        {v: None for v in vertices if v in manter},
        [(a, b) for a, b in arestas if a in manter and b in manter],
    )


def dispor(vertices, arestas):
    """Posição de cada vértice: camadas da esquerda para a direita.

    Devolve `{vertice: (x, y)}` e o conjunto de arestas de retorno, que o
    desenho mostra tracejadas.
    """
    if not vertices:
        return {}, set()

    saida = {v: [] for v in vertices}
    entrada = {v: 0 for v in vertices}
    for a, b in arestas:
        saida[a].append(b)
        entrada[b] += 1

    ordem = _ordem_topologica(vertices, saida, entrada)
    posicao = {v: i for i, v in enumerate(ordem)}

    # Camada = caminho mais longo até aqui. Aresta que anda para trás na ordem
    # topológica é de retorno: entra no desenho, não no cálculo — considerá-la
    # empurraria a camada para o infinito.
    retorno = set()
    camada = {v: 0 for v in vertices}
    for v in ordem:
        for w in saida[v]:
            if posicao[w] > posicao[v]:
                camada[w] = max(camada[w], camada[v] + 1)
            else:
                retorno.add((v, w))

    por_camada = {}
    for v, c in camada.items():
        por_camada.setdefault(c, []).append(v)
    for lista in por_camada.values():
        lista.sort(key=lambda v: v[1])          # ordem inicial estável: por nome

    _reduzir_cruzamentos(por_camada, arestas, camada)

    lugares = {}
    for c, lista in por_camada.items():
        base = -(len(lista) - 1) * ESPACO_Y / 2
        for i, v in enumerate(lista):
            lugares[v] = (c * ESPACO_X, base + i * ESPACO_Y)
    return lugares, retorno


def _ordem_topologica(vertices, saida, entrada):
    """Kahn, mas sem supor que o grafo é acíclico.

    Realimentação é o caso normal num robô. Quando a fila esvazia e ainda sobra
    vértice, o ciclo é quebrado pelo vértice que menos depende do que sobrou —
    escolha arbitrária, mas determinística, que é o que importa para o desenho
    não dançar entre um snapshot e o outro.
    """
    restante = dict(entrada)
    fila = sorted((v for v in vertices if restante[v] == 0))
    vistos = set()
    ordem = []

    while len(ordem) < len(vertices):
        if not fila:
            sobrando = [v for v in vertices if v not in vistos]
            fila = [min(sobrando, key=lambda v: (restante[v], v))]
        v = fila.pop(0)
        if v in vistos:
            continue
        vistos.add(v)
        ordem.append(v)
        for w in saida[v]:
            if w in vistos:
                continue
            restante[w] -= 1
            if restante[w] <= 0:
                fila.append(w)
    return ordem


def _reduzir_cruzamentos(por_camada, arestas, camada, passos=4):
    """Baricentro: cada vértice tenta ficar na altura média dos seus vizinhos.

    Heurística clássica e barata. Não é o mínimo de cruzamentos (isso é NP), mas
    tira o grosso do espaguete em quatro varreduras.
    """
    anteriores = {}
    seguintes = {}
    for a, b in arestas:
        if camada[a] == camada[b]:
            continue
        anteriores.setdefault(b, []).append(a)
        seguintes.setdefault(a, []).append(b)

    indice = {}

    def reindexar():
        for lista in por_camada.values():
            for i, v in enumerate(lista):
                indice[v] = i

    reindexar()
    for passo in range(passos):
        ordem_camadas = sorted(por_camada)
        vizinhos = anteriores
        if passo % 2:
            ordem_camadas.reverse()
            vizinhos = seguintes
        for c in ordem_camadas:
            def peso(v):
                vizinhanca = [indice[u] for u in vizinhos.get(v, []) if u in indice]
                if not vizinhanca:
                    return indice[v]
                return sum(vizinhanca) / len(vizinhanca)
            por_camada[c].sort(key=peso)
            reindexar()


# -- desenho ---------------------------------------------------------------


def _velocidade(hz):
    """Hz medido → voltas por segundo do ponto no fio.

    Escala logarítmica, não linear: taxa de ROS vai de 0,1 Hz (diagnóstico) a
    500 Hz (malha de controle). Numa escala linear tudo abaixo de 50 Hz pareceria
    parado, que é justamente a faixa onde estão os tópicos que se depura.
    """
    if not hz or hz <= 0:
        return 0.0
    return max(0.10, min(1.10, 0.10 + 0.45 * math.log10(1 + hz)))


def _ponta():
    """Triângulo apontando para +x, com a ponta na origem do item.

    Fica em torno da origem porque o item é posicionado no meio da curva e
    girado pelo ângulo da tangente — ponto e rotação, sem recalcular vértice.
    """
    return QtGui.QPolygonF([
        QtCore.QPointF(7.0, 0.0),
        QtCore.QPointF(-6.0, -5.0),
        QtCore.QPointF(-6.0, 5.0),
    ])


class _Aresta(QtWidgets.QGraphicsPathItem):
    """Um fio: quem publica → tópico, ou tópico → quem assina.

    Guarda as duas caixas em vez de coordenadas: arrastar uma caixa é só pedir
    `recalcular()`, sem reconstruir a cena.
    """

    def __init__(self, origem, destino, topico, cores, retorno):
        super().__init__()
        self.origem = origem
        self.destino = destino
        self.topico = topico
        self._cores = cores
        self.velocidade = 0.0
        self.fase = 0.0
        self.pontos = []

        caneta = QtGui.QPen(cores["aresta"], 2.0)
        caneta.setCapStyle(QtCore.Qt.RoundCap)
        if retorno:
            # Tracejado é honestidade: essa aresta anda contra o fluxo do
            # desenho, e sem a marca parece erro de layout.
            caneta.setStyle(QtCore.Qt.DashLine)
        self.setPen(caneta)
        self.setZValue(0)

        # A seta vai no meio da curva, não encostada na caixa. Colada na borda
        # ela some sob o retângulo e vira um pixel escuro — foi o que você viu.
        self.seta = QtWidgets.QGraphicsPolygonItem(_ponta(), self)
        self.seta.setBrush(QtGui.QBrush(cores["seta"]))
        self.seta.setPen(QtGui.QPen(QtCore.Qt.NoPen))

        origem.ligacoes.append(self)
        destino.ligacoes.append(self)
        self.recalcular()

    def recalcular(self):
        x1 = self.origem.x() + self.origem.largura / 2
        x2 = self.destino.x() - self.destino.largura / 2
        y1, y2 = self.origem.y(), self.destino.y()

        caminho = QtGui.QPainterPath(QtCore.QPointF(x1, y1))
        folga = max(40.0, abs(x2 - x1) / 2)
        if x2 < x1:
            # Volta para trás: em linha reta ela passaria por cima das caixas do
            # meio e viraria um risco no meio do desenho. Arco por baixo, que é
            # como um diagrama de blocos desenha realimentação.
            desvio = 80.0 + abs(y2 - y1) / 3
            caminho.cubicTo(x1 + folga, y1 + desvio, x2 - folga, y2 + desvio, x2, y2)
        else:
            caminho.cubicTo(x1 + folga, y1, x2 - folga, y2, x2, y2)
        self.setPath(caminho)

        # A seta fica logo antes da caixa de destino, não no meio da curva: no
        # meio ela cai em cima de outra caixa quando a aresta atravessa o
        # desenho, e some. Aqui ela sempre aparece na folga entre as camadas, e
        # aponta para quem recebe — que é a informação que ela carrega.
        onde = caminho.percentAtLength(max(0.0, caminho.length() - 13.0))
        self.seta.setPos(caminho.pointAtPercent(onde))
        # angleAtPercent devolve em graus anti-horário; o Qt gira horário.
        self.seta.setRotation(-caminho.angleAtPercent(onde))
        self._recolocar()

    # -- fluxo ------------------------------------------------------------

    def animar(self, velocidade):
        self.velocidade = velocidade
        if velocidade > 0 and not self.pontos:
            comprimento = max(self.path().length(), 1.0)
            quantos = max(2, min(6, int(comprimento // 70)))
            for _ in range(quantos):
                ponto = QtWidgets.QGraphicsEllipseItem(-4.5, -4.5, 9, 9, self)
                ponto.setBrush(QtGui.QBrush(self._cores["fluxo"]))
                ponto.setPen(QtGui.QPen(QtCore.Qt.NoPen))
                ponto.setZValue(2)
                self.pontos.append(ponto)
            self._recolocar()
        elif velocidade <= 0 and self.pontos:
            for ponto in self.pontos:
                ponto.setParentItem(None)
                if self.scene() is not None:
                    self.scene().removeItem(ponto)
            self.pontos = []

    def avancar(self, dt):
        if self.velocidade <= 0 or not self.pontos:
            return
        self.fase = (self.fase + self.velocidade * dt) % 1.0
        self._recolocar()

    def _recolocar(self):
        if not self.pontos:
            return
        caminho = self.path()
        quantos = len(self.pontos)
        for i, ponto in enumerate(self.pontos):
            ponto.setPos(caminho.pointAtPercent((self.fase + i / quantos) % 1.0))


class _Caixa(QtWidgets.QGraphicsPathItem):
    """Um nó ou um tópico. Arrastável, e guarda a chave para o clique."""

    def __init__(self, chave, cores, fonte, ao_soltar=None):
        super().__init__()
        self.chave = chave
        self.ligacoes = []
        self.ao_soltar = ao_soltar
        self._cores = cores

        metrica = QtGui.QFontMetrics(fonte)
        rotulo = metrica.elidedText(chave[1], QtCore.Qt.ElideMiddle, LARGURA_MAX)
        largura = min(LARGURA_MAX, metrica.horizontalAdvance(rotulo)) + 20

        caminho = QtGui.QPainterPath()
        raio = 9 if chave[0] == "no" else 2      # nó arredondado, tópico quadrado
        caminho.addRoundedRect(
            -largura / 2, -ALTURA_NO / 2, largura, ALTURA_NO, raio, raio
        )
        self.setPath(caminho)
        self.largura = largura

        texto = QtWidgets.QGraphicsSimpleTextItem(rotulo, self)
        texto.setFont(fonte)
        texto.setBrush(QtGui.QBrush(cores["texto"]))
        retangulo = texto.boundingRect()
        texto.setPos(-retangulo.width() / 2, -retangulo.height() / 2)

        self.setToolTip(chave[1])
        self.setZValue(1)
        self.setFlag(QtWidgets.QGraphicsItem.ItemIsMovable, True)
        self.setFlag(QtWidgets.QGraphicsItem.ItemSendsGeometryChanges, True)
        self.setCursor(QtCore.Qt.OpenHandCursor)
        self.marcar(False)

    def marcar(self, ligado):
        tipo = self.chave[0]
        self.setBrush(QtGui.QBrush(self._cores[f"fundo_{tipo}"]))
        cor = self._cores["selecao"] if ligado else self._cores[f"borda_{tipo}"]
        self.setPen(QtGui.QPen(cor, 2.5 if ligado else 1.2))

    def itemChange(self, mudanca, valor):
        # Arrastou a caixa: os fios acompanham na hora, sem refazer a cena.
        if mudanca == QtWidgets.QGraphicsItem.ItemPositionHasChanged:
            for fio in self.ligacoes:
                fio.recalcular()
        return super().itemChange(mudanca, valor)

    def mouseReleaseEvent(self, evento):
        super().mouseReleaseEvent(evento)
        if self.ao_soltar is not None:
            self.ao_soltar(self)


class Desenho(QtWidgets.QGraphicsView):
    """A tela do grafo. Rolar dá zoom, arrastar o fundo move, arrastar caixa move a caixa."""

    escolheu_no = QtCore.Signal(str)
    escolheu_topico = QtCore.Signal(str)
    fluxo_mudou = QtCore.Signal(str)
    ocultos_mudou = QtCore.Signal(int, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.cena = QtWidgets.QGraphicsScene(self)
        self.setScene(self.cena)
        self.setRenderHint(QtGui.QPainter.Antialiasing)
        # NoDrag, e não ScrollHandDrag: o modo de mão da view engole o evento
        # antes da cena, e aí nenhuma caixa se move. O arrasto do fundo é feito
        # à mão aqui embaixo, justamente para as duas coisas coexistirem.
        self.setDragMode(QtWidgets.QGraphicsView.NoDrag)
        self.setTransformationAnchor(QtWidgets.QGraphicsView.AnchorUnderMouse)
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)

        self._nos = []
        self._topicos = []
        self._termo = ""
        self._ocultos = set()
        self._selecao = {"no": None, "topico": None}
        self._caixas = {}
        self._arestas = []
        self._movidos = {}          # onde você largou cada caixa, se largou
        self._enquadrado = False
        self._clique = None
        self._panorama = None
        self._fluxo = (None, None)  # (topico, hz) do tópico aberto

        fonte = self.font()
        fonte.setPointSizeF(max(7.5, fonte.pointSizeF() - 1))
        self._fonte = fonte

        self._relogio = QtCore.QElapsedTimer()
        self._relogio.start()
        self._animacao = QtCore.QTimer(self)
        self._animacao.setInterval(33)          # ~30 fps, suave e barato
        self._animacao.timeout.connect(self._passo)

    # -- entrada ----------------------------------------------------------

    def atualizar(self, nos, topicos):
        self._nos = nos
        self._topicos = topicos
        self._redesenhar()

    def definir_filtro(self, termo):
        if termo == self._termo:
            return
        self._termo = termo
        self._redesenhar()

    def apagar(self, chave):
        """Ctrl+clique: some com esta caixa e com os fios dela.

        Some do desenho, não do grafo — a tabela continua mostrando tudo, e o
        robô nem fica sabendo. É maquiagem de tela, e é reversível no botão.
        """
        self._ocultos.add(chave)
        self._redesenhar()

    def fluxo(self, topico, hz):
        """Tópico aberto e taxa medida. `hz=None` = ainda não sei, fio parado."""
        self._fluxo = (topico, hz)
        self._aplicar_fluxo()

    def destacar(self, tipo, nome):
        """Seleção veio da tabela: acende a caixa correspondente, se ela existe.

        Nó e tópico são acesos em separado porque as duas tabelas selecionam em
        separado — escolher um tópico não deveria apagar o nó que você filtrou.
        """
        chave = (tipo, nome) if nome else None
        if chave == self._selecao[tipo]:
            return
        anterior = self._caixas.get(self._selecao[tipo])
        if anterior is not None:
            anterior.marcar(False)
        self._selecao[tipo] = chave
        atual = self._caixas.get(chave)
        if atual is not None:
            atual.marcar(True)
            self.ensureVisible(atual, 60, 60)

    def limpar(self):
        self._nos = []
        self._topicos = []
        self._selecao = {"no": None, "topico": None}
        self._fluxo = (None, None)
        self._movidos = {}
        self._ocultos = set()
        self._enquadrado = False
        self._redesenhar()

    def _anunciar_ocultos(self):
        # Caixa apagada sem aviso é caixa que "sumiu sozinha". O contador é o que
        # transforma isso em algo que você sabe desfazer.
        nomes = sorted(nome for _, nome in self._ocultos)
        self.ocultos_mudou.emit(len(nomes), "\n".join(nomes))

    def enquadrar(self):
        retangulo = self.cena.itemsBoundingRect()
        if retangulo.isNull():
            return
        self.fitInView(retangulo.adjusted(-30, -30, 30, 30), QtCore.Qt.KeepAspectRatio)
        escala = self.transform().m11()
        # Dois limites, pelos dois motivos opostos: acima de 1 o desenho vira
        # cartaz e engana sobre o tamanho do que se vê; abaixo de 0,45 o nome do
        # tópico não se lê, e um grafo ilegível inteiro é pior que um pedaço
        # legível — daí ficar nesse piso e deixar você arrastar o resto.
        if escala > 1.0:
            self.resetTransform()
        elif escala < 0.45:
            self.resetTransform()
            self.scale(0.45, 0.45)
            self.centerOn(retangulo.center())

    def reorganizar(self):
        """Volta ao começo: mostra tudo de novo e refaz o layout.

        Um botão só para as duas maneiras de bagunçar a tela — arrastar caixa e
        apagar caixa — porque quando você quer desfazer, quer desfazer tudo.
        """
        self._movidos = {}
        self._ocultos = set()
        self._enquadrado = False
        self._redesenhar()

    # -- construção da cena -----------------------------------------------

    def _redesenhar(self):
        # Guardar e restaurar a câmera: sem isso, cada mudança do grafo (1 Hz)
        # jogaria você de volta para o canto e o zoom se perderia sozinho.
        transformacao = self.transform()
        centro = self.mapToScene(self.viewport().rect().center())

        self._animacao.stop()
        self.cena.clear()          # apaga os itens: nenhuma referência sobrevive
        self._caixas = {}
        self._arestas = []

        vertices, arestas = montar(self._nos, self._topicos, self._termo, self._ocultos)
        self._anunciar_ocultos()

        if not vertices:
            self._recado(
                "sem nada para desenhar"
                if not self._nos else "nada casa com o filtro"
            )
            return
        if len(vertices) > LIMITE:
            self._recado(
                f"{len(vertices)} caixas — grande demais para ler.\n"
                "Use o filtro acima para desenhar só um pedaço."
            )
            return

        cores = self._cores()
        lugares, retorno = dispor(vertices, arestas)

        for v in vertices:
            caixa = _Caixa(v, cores, self._fonte, ao_soltar=self._guardar_lugar)
            # Posição arrastada à mão vence o layout: se você organizou a tela do
            # seu jeito, um nó novo aparecendo não pode desmanchar aquilo.
            caixa.setPos(*self._movidos.get(v, lugares[v]))
            self.cena.addItem(caixa)
            self._caixas[v] = caixa

        for a, b in arestas:
            topico = a[1] if a[0] == "topico" else b[1]
            fio = _Aresta(self._caixas[a], self._caixas[b], topico, cores,
                          (a, b) in retorno)
            self.cena.addItem(fio)
            self._arestas.append(fio)

        for chave in self._selecao.values():
            if chave in self._caixas:
                self._caixas[chave].marcar(True)

        # Folga em volta do conteúdo, senão não há para onde arrastar quando o
        # grafo cabe inteiro na tela — e o desenho parece travado.
        self.cena.setSceneRect(
            self.cena.itemsBoundingRect().adjusted(-500, -500, 500, 500)
        )

        if self._enquadrado:
            self.setTransform(transformacao)
            self.centerOn(centro)
        else:
            self.enquadrar()
            self._enquadrado = True

        self._aplicar_fluxo()

    def _guardar_lugar(self, caixa):
        self._movidos[caixa.chave] = (caixa.x(), caixa.y())

    def _recado(self, texto):
        item = self.cena.addSimpleText(texto, self._fonte)
        item.setBrush(QtGui.QBrush(self.palette().color(QtGui.QPalette.PlaceholderText)))
        self.cena.setSceneRect(item.boundingRect().adjusted(-200, -200, 200, 200))
        self.centerOn(item)

    def _cores(self):
        """Cores tiradas só de `WindowText` e `Highlight`.

        São as duas que a paleta garante contrastarem com o fundo, em tema claro
        e escuro. A primeira versão usava `Mid` para a caixa de tópico e para o
        fio — e no tema escuro `Mid` é quase a cor do fundo: a caixa do tópico
        sumia e sobrava o nome flutuando, como se a ligação não tivesse nome.
        """
        pal = self.palette()
        texto = pal.color(QtGui.QPalette.WindowText)
        destaque = pal.color(QtGui.QPalette.Highlight)
        escuro = pal.color(QtGui.QPalette.Window).lightness() < 128

        def alfa(cor, a):
            copia = QtGui.QColor(cor)
            copia.setAlpha(a)
            return copia

        return {
            "fundo_no": alfa(destaque, 55),
            "borda_no": destaque,
            "fundo_topico": alfa(texto, 30),
            "borda_topico": alfa(texto, 160),
            "texto": texto,
            "aresta": alfa(texto, 150),
            "seta": alfa(texto, 235),
            "fluxo": destaque,
            # Clarear no escuro, escurecer no claro: `darker` nos dois casos
            # deixaria a seleção invisível justamente no tema escuro.
            "selecao": destaque.lighter(135) if escuro else destaque.darker(140),
        }

    # -- fluxo -------------------------------------------------------------

    def _aplicar_fluxo(self):
        topico, hz = self._fluxo

        # Dizer o que está animando, e por quê não está. A animação só existe no
        # tópico aberto (§3), então sem esse aviso quem não clicou em nada
        # conclui que o fluxo não funciona.
        if not topico:
            self.fluxo_mudou.emit("fluxo: clique num tópico para ver os pacotes")
        elif not hz:
            self.fluxo_mudou.emit(f"fluxo: {topico} · medindo…")
        else:
            self.fluxo_mudou.emit(f"fluxo: {topico} · {hz:.1f} Hz")

        velocidade = _velocidade(hz)
        vivas = 0
        for fio in self._arestas:
            anda = velocidade if fio.topico == topico else 0.0
            fio.animar(anda)
            vivas += anda > 0

        # Timer só existe enquanto há o que animar. Um QTimer de 30 fps girando
        # à toa num robô emprestado é custo que ninguém pediu.
        if vivas and not self._animacao.isActive():
            self._relogio.restart()
            self._animacao.start()
        elif not vivas and self._animacao.isActive():
            self._animacao.stop()

    @QtCore.Slot()
    def _passo(self):
        dt = min(self._relogio.restart() / 1000.0, 0.1)
        for fio in self._arestas:
            fio.avancar(dt)

    # -- interação --------------------------------------------------------

    def wheelEvent(self, evento):
        fator = 1.15 if evento.angleDelta().y() > 0 else 1 / 1.15
        self.scale(fator, fator)

    def _caixa_em(self, ponto):
        item = self.itemAt(ponto.toPoint())
        while item is not None and not isinstance(item, _Caixa):
            item = item.parentItem()
        return item

    def mousePressEvent(self, evento):
        self._clique = evento.position()
        if evento.button() == QtCore.Qt.LeftButton and self._caixa_em(evento.position()) is None:
            # Fundo: arrasta a tela. Em cima de caixa, deixa a cena cuidar, que é
            # quem move a caixa.
            self._panorama = evento.position()
            self.viewport().setCursor(QtCore.Qt.ClosedHandCursor)
            return
        super().mousePressEvent(evento)

    def mouseMoveEvent(self, evento):
        if self._panorama is not None:
            delta = evento.position() - self._panorama
            self._panorama = evento.position()
            h = self.horizontalScrollBar()
            v = self.verticalScrollBar()
            h.setValue(h.value() - int(delta.x()))
            v.setValue(v.value() - int(delta.y()))
            return
        super().mouseMoveEvent(evento)

    def mouseReleaseEvent(self, evento):
        arrastava = self._panorama is not None
        self._panorama = None
        self.viewport().unsetCursor()
        if not arrastava:
            super().mouseReleaseEvent(evento)

        # Arrastar move, clicar escolhe. A diferença é a distância: sem esse
        # limiar, todo fim de arrasto selecionaria a caixa de baixo.
        if self._clique is None:
            return
        andou = (evento.position() - self._clique).manhattanLength()
        self._clique = None
        if andou > 4:
            return

        caixa = self._caixa_em(evento.position())
        if caixa is None:
            return

        if evento.modifiers() & QtCore.Qt.ControlModifier:
            self.apagar(caixa.chave)
            return

        tipo, nome = caixa.chave
        if tipo == "no":
            self.escolheu_no.emit(nome)
        else:
            self.escolheu_topico.emit(nome)


class PainelDesenho(QtWidgets.QWidget):
    """O desenho mais os controles que ele precisa."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.desenho = Desenho()

        self.ocultos = QtWidgets.QLabel()
        self.ocultos.setEnabled(False)

        self.reorganizar = QtWidgets.QPushButton("reorganizar")
        self.reorganizar.setToolTip(
            "Volta ao estado inicial: devolve tudo que você apagou com "
            "ctrl+clique e refaz o layout do zero."
        )
        self.enquadrar = QtWidgets.QPushButton("enquadrar")

        self.dica = QtWidgets.QLabel(
            "rolar = zoom · arrastar = mover · ctrl+clique = apagar"
        )
        self.dica.setEnabled(False)

        self.fluxo = QtWidgets.QLabel("fluxo: clique num tópico para ver os pacotes")
        self.fluxo.setToolTip(
            "Hz só existe para o tópico assinado: ROS 2 não informa taxa de "
            "publicação sem assinar, e assinar tudo violaria o §3. Fio parado "
            "quer dizer 'não estou medindo isto', não 'não passa nada aqui'."
        )

        topo = QtWidgets.QHBoxLayout()
        topo.setContentsMargins(0, 0, 0, 0)
        topo.addWidget(self.ocultos)
        topo.addSpacing(16)
        topo.addWidget(self.fluxo)
        topo.addStretch()
        topo.addWidget(self.dica)
        topo.addWidget(self.reorganizar)
        topo.addWidget(self.enquadrar)

        fora = QtWidgets.QVBoxLayout(self)
        fora.setContentsMargins(0, 0, 0, 0)
        fora.addLayout(topo)
        fora.addWidget(self.desenho, stretch=1)

        self.desenho.fluxo_mudou.connect(self.fluxo.setText)
        self.desenho.ocultos_mudou.connect(self._mostrar_ocultos)
        self.enquadrar.clicked.connect(self.desenho.enquadrar)
        self.reorganizar.clicked.connect(self.desenho.reorganizar)

    @QtCore.Slot(int, str)
    def _mostrar_ocultos(self, quantos, nomes):
        self.ocultos.setText(
            "nada apagado" if not quantos else f"{quantos} apagados do desenho"
        )
        self.ocultos.setToolTip(nomes or "ctrl+clique numa caixa para apagá-la")
