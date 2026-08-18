"""A janela. Duas páginas: conectar e, depois de conectado, o grafo.

O formulário some quando a sessão sobe — ele já cumpriu o papel dele, e manter
campo de senha na tela durante o trabalho é ruído. O que não some é o rodapé:
"em qual máquina eu estou" é informação de primeira classe (§7) e fica visível o
tempo todo, junto com o botão de desconectar e o acesso ao diagnóstico.
"""

from PySide6 import QtCore, QtGui, QtWidgets

from src.connection import SshSession
from src.graph import GraphPage
from src.historico import Historico

PAGINA_CONEXAO = 0
PAGINA_GRAFO = 1


class MyWidget(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("T1 Debug")
        self.session = None
        self.historico = Historico()

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

        self.setup = QtWidgets.QLineEdit()
        self.setup.setPlaceholderText("/opt/ros/humble/setup.bash (opcional)")

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
        if recente:
            self.setup.setText(recente["setup"])
        return pagina

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
        self.setup.setText(self.historico.setup_de(alvo))
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

        self.desconectar_btn = QtWidgets.QPushButton("Desconectar")
        self.desconectar_btn.setVisible(False)

        linha = QtWidgets.QHBoxLayout()
        linha.addWidget(self.status)
        linha.addStretch()
        linha.addWidget(self.ver_diagnostico)
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

        self.session = SshSession(self)
        self.session.connected.connect(self.on_connected)
        self.session.failed.connect(self.on_failed)
        self.session.log.connect(self.on_log)
        self.session.message.connect(self.on_message)
        self.session.closed.connect(self.on_closed)
        self.session.start(
            self.ssh.currentText().strip(),
            self.password.text(),
            self.setup.text().strip(),
        )

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
        erro = self.historico.registrar(alvo, self.setup.text().strip())
        if erro:
            self.diagnostico.appendPlainText(erro)
        self._carregar_historico(manter=alvo)

        self.desconectar_btn.setVisible(True)
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

    @QtCore.Slot(str)
    def on_failed(self, msg):
        self.status.setText("falha na conexão")
        self.diagnostico.appendPlainText(msg)
        self.ver_diagnostico.setChecked(True)
        self.reset()

    @QtCore.Slot()
    def on_closed(self):
        self.status.setText("sessão encerrada")
        self.reset()

    def reset(self):
        self.session = None
        self.grafo.encerrar()
        self.setWindowTitle("T1 Debug")
        self.desconectar_btn.setVisible(False)
        self.pilha.setCurrentIndex(PAGINA_CONEXAO)
        self.set_busy(False)
        self.update_button()

    def closeEvent(self, event):
        # Fechar a janela derruba o SSH, o agente recebe EOF e morre (§1).
        self.desconectar()
        super().closeEvent(event)
