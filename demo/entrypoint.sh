#!/bin/bash
# PID 1 do container: sobe o sshd e os nós demo, depois fica de pé.
set -e

# -D mantém em primeiro plano seria o normal, mas aqui o sshd vai para o fundo
# porque o processo que precisa segurar o container é o dos nós demo — se eles
# morrerem, o container morre e você percebe, em vez de ficar com um alvo de SSH
# que não tem grafo nenhum.
/usr/sbin/sshd -e

echo "[demo] sshd de pé na porta 22 (container)"

# Os nós rodam como `robo`, o mesmo usuário que a GUI usa no SSH. Mesmo usuário
# e mesmo container = descoberta DDS por localhost funciona sem configuração.
exec su robo -c /usr/local/bin/demo-nodes.sh
