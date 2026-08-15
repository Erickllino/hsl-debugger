from PySide6 import QtCore, QtWidgets
import os

class MyWidget(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("HSL Debugger")

        self.ssh = QtWidgets.QLineEdit()
        self.ssh.setPlaceholderText("user@host")
        self.ssh.setClearButtonEnabled(True)

        self.password = QtWidgets.QLineEdit()
        self.password.setPlaceholderText("password")
        self.password.setEchoMode(QtWidgets.QLineEdit.Password)

        self.button = QtWidgets.QPushButton("Connect")
        self.button.setDefault(True)
        self.button.setEnabled(False)

        form = QtWidgets.QFormLayout()
        form.addRow("SSH", self.ssh)
        form.addRow("Password", self.password)
        form.addRow("", self.button)

        # Keep the form at a readable width, centred in whatever size the
        # window is given.
        box = QtWidgets.QWidget()
        box.setLayout(form)
        box.setMaximumWidth(400)

        outer = QtWidgets.QVBoxLayout(self)
        outer.addStretch()
        outer.addWidget(box, alignment=QtCore.Qt.AlignHCenter)
        outer.addStretch()

        self.ssh.textChanged.connect(self.update_button)
        self.password.textChanged.connect(self.update_button)
        self.ssh.returnPressed.connect(self.password.setFocus)
        self.password.returnPressed.connect(self.button.click)
        self.button.clicked.connect(self.magic)

    @QtCore.Slot()
    def update_button(self):
        self.button.setEnabled(
            bool(self.ssh.text().strip()) and bool(self.password.text())
        )

    @QtCore.Slot()
    def magic(self):

        os.system(f'ssh {self.ssh.text().strip()}')
        
        print(self.ssh.text().strip())
        #evrc@slurm-client1.cin.ufpe.br