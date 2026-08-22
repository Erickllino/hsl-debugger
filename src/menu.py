"""A janela. Duas páginas: conectar e, depois de conectado, o grafo.

O formulário some quando a sessão sobe — ele já cumpriu o papel dele, e manter
campo de senha na tela durante o trabalho é ruído. O que não some é o rodapé:
"em qual máquina eu estou" é informação de primeira classe (§7) e fica visível o
tempo todo, junto com o botão de desconectar e o acesso ao diagnóstico.

O login pergunta o mínimo para conectar: endereço, senha e *uma* fonte de ROS.
Que falta uma segunda fonte não é coisa que se saiba na hora de logar — descobre-se
depois, vendo tópico com tipo ilegível (§5). Por isso acrescentar fonte é operação
do rodapé, não do formulário: `adicionar_setup`.
"""

from PySide6 import QtCore, QtGui, QtWidgets

from src.connection import SETUP_PADRAO, SshSession, fontes_de_setup
from src.graph import GraphPage
from src.historico import Historico

PAGINA_CONEXAO = 0
PAGINA_GRAFO = 1

# O bootstrap (§5) já diz no `stderr` o que carregou e que overlay existe e ficou
# de fora. Ler essas duas linhas é o que permite oferecer o caminho pronto em vez
# de pedir para o usuário adivinhar onde fica o workspace no robô dos outros.
PREFIXO_CARREGADO = "carregado: "
PREFIXO_OVERLAY = "overlay disponível, não carregado: "


class MyWidget(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("T1 Debug")
        self.session = None
        self.historico = Historico()

        # Fontes de ROS acrescentadas depois do login. Ficam separadas do campo
        # para o formulário continuar sendo de uma linha; na hora de conectar as
        # duas viram uma lista só (`_fontes`).
        self._extras = []
        # O que o bootstrap relatou na conexão atual: carregado e disponível.
        self._carregados = []
        self._overlays = []
        # §6 permite senha em memória durante a sessão — o que não pode existir
        # é persistir. Guardar aqui é o que deixa o reconectar funcionar sem
        # pedir a senha de novo a cada fonte acrescentada.
        self._senha = ""
        self._reconectando = False

        self.pilha = QtWidgets.QStackedWidget()
        self.pilha.addWidget(self._pagina_conexao())

        self.grafo = GraphPage()
        self.pilha.addWidget(self.grafo)

        # Painel de diagnóstico: é aqui que aparece o stderr do ssh e do agente,
        # que é o que se olha quando o ambiente ROS não é encontrado (§5).
        self.diagnostico = QtWidgets.QPlainTextEdit()
        self.diagnostico.setReadOnly(True)
        self.diagnostico.setMaximumBlockCount(500)
        self.diagnostico.setVisible(False)

        corpo = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        corpo.addWidget(self.pilha)
        corpo.addWidget(self.diagnostico)
        corpo.setStretchFactor(0, 4)
        corpo.setStretchFactor(1, 1)

        outer = QtWidgets.QVBoxLayout(self)
        outer.addWidget(corpo, stretch=1)
        outer.addLayout(self._rodape())

        self.ssh.currentTextChanged.connect(self.update_button)
        self.ssh.currentTextChanged.connect(self._atualizar_esquecer)
        self.ssh.activated.connect(self._escolher_historico)
        self.esquecer_acao.triggered.connect(self._esquecer_atual)
        self.ssh.lineEdit().returnPressed.connect(self.password.setFocus)
        self.password.returnPressed.connect(self.button.click)
        self.button.clicked.connect(self.magic)
        self.setup.returnPressed.connect(self.button.click)
        self.mais_setup_btn.clicked.connect(self.adicionar_setup)
        self.desconectar_btn.clicked.connect(self.desconectar)
        self.ver_diagnostico.toggled.connect(self.diagnostico.setVisible)

        # A página do grafo não conhece SSH: ela pede, o menu manda (§7).
        self.grafo.assinar.connect(self.on_assinar)
        self.grafo.desassinar.connect(self.on_desassinar)

    # -- construção -------------------------------------------------------

    def _pagina_conexao(self):
        # Combo editável em vez de campo de texto: o endereço continua digitável,
        # mas os que já funcionaram ficam a um clique. O histórico só guarda
        # endereço e caminho de setup — senha, nunca (§6).
        self.ssh = QtWidgets.QComboBox()
        self.ssh.setEditable(True)
        self.ssh.setInsertPolicy(QtWidgets.QComboBox.NoInsert)
        self.ssh.lineEdit().setPlaceholderText("usuario@robo")
        self.ssh.lineEdit().setClearButtonEnabled(True)
        self.ssh.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed
        )

        self.esquecer_acao = QtGui.QAction(
            self.style().standardIcon(QtWidgets.QStyle.SP_DialogDiscardButton),
            "esquecer este endereço",
            self,
        )
        self.esquecer_acao.setVisible(False)
        self.ssh.lineEdit().addAction(
            self.esquecer_acao, QtWidgets.QLineEdit.TrailingPosition
        )

        # Senha fica só em memória, e some assim que a sessão sobe. Não existe
        # "lembrar senha" aqui, por decisão (§6): o caminho recomendado é
        # ssh-keygen + ssh-copy-id, e aí o campo fica vazio para sempre.
        self.password = QtWidgets.QLineEdit()
        self.password.setPlaceholderText("senha (vazio = chave SSH)")
        self.password.setEchoMode(QtWidgets.QLineEdit.Password)

        # Uma linha. Vazio continua sendo o caminho normal — aí quem procura é
        # o bootstrap. A segunda fonte, quando precisa, entra pelo rodapé depois
        # de conectado, onde já dá para ver *que* ela faz falta.
        self.setup = QtWidgets.QLineEdit()
        # Curto de propósito: o campo é estreito e o texto longo era elidido no
        # meio, virando "vazio = procurar sozinh...". O exemplo vai na dica.
        self.setup.setPlaceholderText("vazio = procurar sozinho")
        self.setup.setToolTip(
            f"Caminho de um setup.bash no robô. Já vem em {SETUP_PADRAO},\n"
            "que é o workspace do time; apagar volta ao automático.\n"
            "Vazio: o robô é vasculhado (/opt/ros e ~/*_ws).\n"
            "Mais fontes: botão \u201c+ setup ROS\u201d, já conectado."
        )
        self.setup.setClearButtonEnabled(True)

        self.button = QtWidgets.QPushButton("Conectar")
        self.button.setDefault(True)
        self.button.setEnabled(False)

        form = QtWidgets.QFormLayout()
        form.addRow("SSH", self.ssh)
        form.addRow("Senha", self.password)
        form.addRow("Setup ROS", self.setup)
        form.addRow("", self.button)

        box = QtWidgets.QWidget()
        box.setLayout(form)
        box.setMaximumWidth(460)

        centro = QtWidgets.QVBoxLayout()
        centro.addStretch()
        centro.addWidget(box, alignment=QtCore.Qt.AlignHCenter)
        centro.addStretch()

        pagina = QtWidgets.QWidget()
        pagina.setLayout(centro)

        # Abrir já com o último robô preenchido é o §7: o host padrão vem do
        # perfil. Preenche, não conecta — decidir conectar continua sendo seu.
        recente = self.historico.mais_recente()
        self._carregar_historico(manter=recente["alvo"] if recente else "")
        # Histórico vence o padrão, inclusive quando o que funcionou naquele robô
        # foi o campo vazio: o perfil lembra o que deu certo, e sobrescrever isso
        # com um palpite desfaria a descoberta da última vez (§5). Sem histórico,
        # o campo já abre no workspace do time.
        self._carregar_setup(recente["setup"] if recente else SETUP_PADRAO)
        return pagina

    def _carregar_setup(self, texto):
        """Fontes salvas → campo de uma linha + extras.

        O histórico guarda a lista inteira que funcionou, inclusive o que foi
        acrescentado no meio da sessão anterior — é a metade útil dele (§5), e
        jogar fora o que não coube no campo seria perder justamente a parte
        difícil de descobrir. Então a primeira vai para o campo e as demais
        voltam como extras, visíveis no diálogo do rodapé.
        """
        try:
            fontes = fontes_de_setup(texto)
        except ValueError:
            # Registro editado à mão com aspa simples: o campo mostra cru para o
            # usuário ver e corrigir, em vez de sumir com o conteúdo.
            self.setup.setText(texto.strip())
            self._extras = []
            self.setup.setCursorPosition(0)
            return
        self.setup.setText(fontes[0] if fontes else "")
        self._extras = fontes[1:]
        # O campo é mais estreito que o caminho, e `setText` deixa o cursor no
        # fim: sem isto a tela abre mostrando "…/install/setup.bash", que é a
        # metade que não identifica nada. O começo é o que diz qual workspace é.
        self.setup.setCursorPosition(0)

    # -- histórico --------------------------------------------------------

    def _carregar_historico(self, manter=None):
        """Redesenha a lista do combo sem perder o que está digitado."""
        texto = manter if manter is not None else self.ssh.currentText()

        self.ssh.blockSignals(True)
        self.ssh.clear()
        for e in self.historico.entradas:
            self.ssh.addItem(e["alvo"])
            if e["ultimo"]:
                self.ssh.setItemData(
                    self.ssh.count() - 1,
                    f"última conexão: {e['ultimo'].replace('T', ' ')}",
                    QtCore.Qt.ToolTipRole,
                )
        self.ssh.setCurrentText(texto)
        self.ssh.blockSignals(False)

        self._atualizar_esquecer()
        self.update_button()

    @QtCore.Slot()
    def _atualizar_esquecer(self):
        # O botão de esquecer só existe para endereço que está no histórico.
        atual = self.ssh.currentText().strip()
        self.esquecer_acao.setVisible(self.historico.entrada(atual) is not None)

    @QtCore.Slot(int)
    def _escolher_historico(self, indice):
        """Escolha na lista: traz junto o setup.bash que funcionou daquela vez.

        É a metade útil do histórico. Lembrar o endereço e esquecer o caminho do
        setup deixaria de fora justamente a parte difícil de descobrir (§5).
        """
        alvo = self.ssh.itemText(indice)
        self._carregar_setup(self.historico.setup_de(alvo))
        self._atualizar_esquecer()

    @QtCore.Slot()
    def _esquecer_atual(self):
        alvo = self.ssh.currentText().strip()
        erro = self.historico.esquecer(alvo)
        if erro:
            self.diagnostico.appendPlainText(erro)
            self.ver_diagnostico.setChecked(True)
        self._carregar_historico(manter=alvo)

    def _rodape(self):
        self.status = QtWidgets.QLabel("desconectado")

        self.ver_diagnostico = QtWidgets.QToolButton()
        self.ver_diagnostico.setText("diagnóstico")
        self.ver_diagnostico.setCheckable(True)

        # Fica ao lado do Desconectar porque as duas ações são da sessão, não
        # da página: acrescentar fonte é reabrir a sessão (ver `adicionar_setup`).
        self.mais_setup_btn = QtWidgets.QToolButton()
        self.mais_setup_btn.setText("+ setup ROS")
        self.mais_setup_btn.setVisible(False)

        self.desconectar_btn = QtWidgets.QPushButton("Desconectar")
        self.desconectar_btn.setVisible(False)

        linha = QtWidgets.QHBoxLayout()
        linha.addWidget(self.status)
        linha.addStretch()
        linha.addWidget(self.ver_diagnostico)
        linha.addWidget(self.mais_setup_btn)
        linha.addWidget(self.desconectar_btn)
        return linha

    # -- ações ------------------------------------------------------------

    @QtCore.Slot()
    def update_button(self):
        # Só o alvo é obrigatório: sem senha, o ssh usa chave.
        self.button.setEnabled(bool(self.ssh.currentText().strip()))

    @QtCore.Slot()
    def magic(self):
        """Abre a sessão e sobe o agente. Não bloqueia: tudo volta por Signal."""
        if self.session is not None:
            self.desconectar()
            return

        self.diagnostico.clear()
        self.ver_diagnostico.setChecked(True)
        self.set_busy(True)
        self.status.setText("conectando…")

        # O relatório do bootstrap vale por conexão: a lista de fontes mudou, o
        # que estava carregado antes já não descreve esta sessão.
        self._carregados = []
        self._overlays = []
        # Campo vazio no reconectar não significa "sem senha" — significa que a
        # sessão anterior já a limpou da tela (§6). A da memória continua valendo
        # até desconectar de verdade.
        self._senha = self.password.text() or self._senha

        self.session = SshSession(self)
        self.session.connected.connect(self.on_connected)
        self.session.failed.connect(self.on_failed)
        self.session.log.connect(self.on_log)
        self.session.message.connect(self.on_message)
        self.session.closed.connect(self.on_closed)
        self.session.start(
            self.ssh.currentText().strip(),
            self._senha,
            self._setup(),
        )

    def _fontes(self):
        """A lista completa: o que está no campo, depois o que foi acrescentado.

        A ordem é a ordem de carregamento no robô — underlay primeiro, overlay
        depois — então extra acrescentado por último entra por último, que é o
        que se quer de um overlay.
        """
        try:
            base = fontes_de_setup(self.setup.text())
        except ValueError:
            base = [self.setup.text().strip()]  # deixa o bootstrap recusar
        return base + self._extras

    def _setup(self):
        """As fontes no formato que a `SshSession` espera: uma por linha."""
        return "\n".join(self._fontes())

    @QtCore.Slot()
    def adicionar_setup(self):
        """Acrescenta uma fonte de ROS à sessão — o que obriga a reabri-la.

        Não dá para sourcear um `setup.bash` dentro de um agente que já está
        rodando: o bootstrap carrega o ambiente **antes** do `python3` existir
        (§5), e `AMENT_PREFIX_PATH`/`PYTHONPATH` são lidos no import. Então
        acrescentar fonte é derrubar o agente e subir outro com a lista nova.
        Isso é barato justamente porque o agente é efêmero por construção (§3) —
        o que se perde é a lista de tópicos abertos, não estado no robô.
        """
        atuais = self._carregados or self._fontes()
        rotulo = "Carregado nesta sessão:\n  " + ("\n  ".join(atuais) or "(nada)")
        if self._overlays:
            rotulo += "\n\nO robô tem estes, ainda não carregados:"
        rotulo += "\n\nAcrescentar fonte (a sessão será reaberta):"

        # Editável com os overlays que o bootstrap encontrou já na lista: o
        # caminho do workspace no robô dos outros é exatamente o que ninguém
        # sabe de cor, e ele já apareceu no diagnóstico.
        caminho, ok = QtWidgets.QInputDialog.getItem(
            self, "Adicionar setup ROS", rotulo, self._overlays, 0, True
        )
        if not ok:
            return

        try:
            novas = fontes_de_setup(caminho)
        except ValueError as exc:
            self.diagnostico.appendPlainText(str(exc))
            self.ver_diagnostico.setChecked(True)
            return
        # Já carregado é acerto do usuário, não erro: dizer e não fazer nada é
        # melhor do que reabrir a sessão para chegar no mesmo lugar.
        novas = [c for c in novas if c not in self._fontes() and c not in atuais]
        if not novas:
            self.status.setText("essa fonte já está carregada")
            return

        self._extras += novas
        self._reconectar()

    def _reconectar(self):
        """Derruba a sessão e sobe outra com a lista de fontes atual.

        Em duas etapas por `on_closed`, e não aqui, porque `stop()` espera o
        processo morrer: subir a sessão nova de dentro dessa espera criaria uma
        enquanto a outra ainda está sendo desmontada.
        """
        if self.session is None:
            return
        self._reconectando = True
        self.status.setText("recarregando ambiente…")
        self.desconectar()

    def desconectar(self):
        if self.session is not None:
            self.session.stop()

    def set_busy(self, busy):
        self.ssh.lineEdit().setReadOnly(busy)
        for w in (self.password, self.setup):
            w.setReadOnly(busy)
        self.button.setEnabled(not busy)

    @QtCore.Slot(str)
    def on_assinar(self, topico):
        if self.session is not None:
            self.session.send({"cmd": "assinar", "topico": topico})

    @QtCore.Slot(str)
    def on_desassinar(self, topico):
        if self.session is not None:
            self.session.send({"cmd": "desassinar", "topico": topico})

    # -- retornos da sessão -----------------------------------------------

    @QtCore.Slot(str, str)
    def on_connected(self, distro, host):
        # Em qual máquina eu estou é informação de primeira classe (§7).
        self.setWindowTitle(f"T1 Debug — {host}")
        self.status.setText(f"conectado a {host} (ROS {distro})")
        self.password.clear()

        # Só agora, e nunca antes: endereço que não conectou não vira histórico.
        # `password` não entra aqui nem por engano — `registrar` não tem esse
        # parâmetro (§6).
        alvo = self.ssh.currentText().strip()
        erro = self.historico.registrar(alvo, self._setup())
        if erro:
            self.diagnostico.appendPlainText(erro)
        self._carregar_historico(manter=alvo)

        self.desconectar_btn.setVisible(True)
        self.mais_setup_btn.setVisible(True)
        carregado = self._carregados or self._fontes()
        self.mais_setup_btn.setToolTip(
            "Carregado:\n  " + ("\n  ".join(carregado) or "(nada relatado)")
        )
        self.pilha.setCurrentIndex(PAGINA_GRAFO)
        # Conectado e sem erro: o diagnóstico já não é o que interessa ver.
        self.ver_diagnostico.setChecked(False)

    @QtCore.Slot(dict)
    def on_message(self, msg):
        tipo = msg.get("tipo")
        if tipo == "grafo":
            self.grafo.atualizar(msg.get("nos", []), msg.get("topicos", []))
        elif tipo == "msg":
            self.grafo.receber_msg(msg)
        elif tipo == "status":
            texto = msg.get("msg", "")
            self.diagnostico.appendPlainText(f"[{msg.get('nivel', '?')}] {texto}")
            self.grafo.receber_status(msg)
        elif tipo == "pong":
            pass
        else:
            self.diagnostico.appendPlainText(f"mensagem desconhecida: {tipo!r}")

    @QtCore.Slot(str)
    def on_log(self, linha):
        self.diagnostico.appendPlainText(linha)

        # O bootstrap já diz o que carregou e que overlay deixou de fora (§5).
        # Guardar as duas coisas é o que faz o diálogo do "+ setup ROS" chegar
        # com o caminho pronto em vez de uma caixa de texto vazia.
        if linha.startswith(PREFIXO_CARREGADO):
            caminho = linha[len(PREFIXO_CARREGADO):].strip()
            if caminho not in self._carregados:
                self._carregados.append(caminho)
        elif linha.startswith(PREFIXO_OVERLAY):
            caminho = linha[len(PREFIXO_OVERLAY):].strip()
            if caminho not in self._overlays:
                self._overlays.append(caminho)

    @QtCore.Slot(str)
    def on_failed(self, msg):
        self.status.setText("falha na conexão")
        self.diagnostico.appendPlainText(msg)
        self.ver_diagnostico.setChecked(True)
        self.reset()

    @QtCore.Slot()
    def on_closed(self):
        if self._reconectando:
            self.reset()  # ainda com a bandeira de pé: é ela que segura a senha
            self._reconectando = False
            # Fora da pilha do `stop()`, para a sessão nova não nascer dentro da
            # desmontagem da antiga.
            QtCore.QTimer.singleShot(0, self.magic)
            return
        self.status.setText("sessão encerrada")
        self.reset()

    def reset(self):
        self.session = None
        self.grafo.encerrar()
        self.setWindowTitle("T1 Debug")
        self.desconectar_btn.setVisible(False)
        self.mais_setup_btn.setVisible(False)
        self.pilha.setCurrentIndex(PAGINA_CONEXAO)
        self.set_busy(False)
        self.update_button()
        if not self._reconectando:
            # Sessão acabou de verdade: a senha em memória morre com ela (§6).
            self._senha = ""

    def closeEvent(self, event):
        # Fechar a janela derruba o SSH, o agente recebe EOF e morre (§1).
        self.desconectar()
        super().closeEvent(event)
