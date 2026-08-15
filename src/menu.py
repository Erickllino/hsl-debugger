from PySide6 import QtCore, QtWidgets

from src.connection import SshSession


class MyWidget(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("T1 Debug")
        self.session = None

        self.ssh = QtWidgets.QLineEdit()
        self.ssh.setPlaceholderText("usuario@robo")
        self.ssh.setClearButtonEnabled(True)

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

        self.status = QtWidgets.QLabel("desconectado")
        self.status.setAlignment(QtCore.Qt.AlignCenter)

        form = QtWidgets.QFormLayout()
        form.addRow("SSH", self.ssh)
        form.addRow("Senha", self.password)
        form.addRow("Setup ROS", self.setup)
        form.addRow("", self.button)
        form.addRow("", self.status)

        box = QtWidgets.QWidget()
        box.setLayout(form)
        box.setMaximumWidth(460)

        # Painel de diagnóstico: é aqui que aparece o stderr do ssh e do agente,
        # que é o que se olha quando o ambiente ROS não é encontrado (§5).
        self.diagnostico = QtWidgets.QPlainTextEdit()
        self.diagnostico.setReadOnly(True)
        self.diagnostico.setMaximumBlockCount(500)
        self.diagnostico.setVisible(False)

        outer = QtWidgets.QVBoxLayout(self)
        outer.addStretch()
        outer.addWidget(box, alignment=QtCore.Qt.AlignHCenter)
        outer.addStretch()
        outer.addWidget(self.diagnostico, stretch=1)

        self.ssh.textChanged.connect(self.update_button)
        self.ssh.returnPressed.connect(self.password.setFocus)
        self.password.returnPressed.connect(self.button.click)
        self.button.clicked.connect(self.magic)

    @QtCore.Slot()
    def update_button(self):
        # Só o alvo é obrigatório: sem senha, o ssh usa chave.
        self.button.setEnabled(bool(self.ssh.text().strip()))

    @QtCore.Slot()
    def magic(self):
        """Abre a sessão e sobe o agente. Não bloqueia: tudo volta por Signal."""
        if self.session is not None:
            self.desconectar()
            return

        self.diagnostico.clear()
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

    def desconectar(self):
        if self.session is not None:
            self.session.stop()

    def set_busy(self, busy):
        for w in (self.ssh, self.password, self.setup):
            w.setReadOnly(busy)
        self.button.setEnabled(not busy)

    # -- retornos da sessão -----------------------------------------------

    @QtCore.Slot(str, str)
    def on_connected(self, distro, host):
        # Em qual máquina eu estou é informação de primeira classe (§7).
        self.setWindowTitle(f"T1 Debug — {host}")
        self.status.setText(f"conectado a {host} (ROS {distro})")
        self.password.clear()
        self.button.setText("Desconectar")
        self.button.setEnabled(True)

    @QtCore.Slot(dict)
    def on_message(self, msg):
        # v0.1 pluga aqui: grafo vira tabela de nós e tópicos.
        pass

    @QtCore.Slot(str)
    def on_log(self, linha):
        self.diagnostico.appendPlainText(linha)

    @QtCore.Slot(str)
    def on_failed(self, msg):
        self.status.setText("falha na conexão")
        self.diagnostico.appendPlainText(msg)
        self.reset()

    @QtCore.Slot()
    def on_closed(self):
        self.status.setText("sessão encerrada")
        self.reset()

    def reset(self):
        self.session = None
        self.setWindowTitle("T1 Debug")
        self.button.setText("Conectar")
        self.set_busy(False)
        self.update_button()

    def closeEvent(self, event):
        # Fechar a janela derruba o SSH, o agente recebe EOF e morre (§1).
        self.desconectar()
        super().closeEvent(event)
