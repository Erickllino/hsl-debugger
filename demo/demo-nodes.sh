#!/bin/bash
# Grafo do robô de mentira: o suficiente para exercitar cada coisa que a GUI
# precisa saber mostrar.
#
# Três coisas aqui não são enfeite:
#
# - `/sensor` usa uma mensagem que só existe no workspace de /hsl-player, que o
#   bootstrap não varre sozinho. Sem esse setup.bash informado no campo *Setup
#   ROS*, a GUI mostra o tópico com o tipo pintado e se recusa a assinar — é o
#   caso das mensagens do SDK do Booster (§5);
# - `add_two_ints_server` é um nó que não publica nem assina tópico nenhum. É
#   como um nó de serviço aparece na lista;
# - o laço do `parameter_blackboard` sobe e derruba um nó de serviço a cada 7 s.
#   É esse que faz a lista de nós piscar, e é ele que a caixa "ocultar serviços"
#   existe para tirar da frente.
set -e

# Os nós do robô sourceiam tudo — o problema do §5 é do *agente*, que chega por
# SSH num shell sem nada, não de quem já mora aqui dentro.
source /opt/ros/humble/setup.bash
source /hsl-player/install/setup.bash

echo "[demo] ROS_DISTRO=$ROS_DISTRO ROS_DOMAIN_ID=${ROS_DOMAIN_ID:-0}"

ros2 run demo_nodes_cpp talker &
ros2 run demo_nodes_cpp listener &
ros2 run demo_nodes_cpp add_two_ints_server &

ros2 topic pub -r 2 /sensor t1_robo_msgs/msg/Sensor \
    '{sensor: "imu", valor: 9.81, confiavel: true}' &

# Nó de serviço que pisca: vive 4 s, morre, volta 3 s depois. O
# `parameter_blackboard` serve só parâmetro, então não tem tópico próprio — é a
# forma mais limpa de um nó de serviço aparecer e sumir da lista.
#
# 4 s e não meio segundo de propósito: o snapshot vai a 1 Hz e a descoberta do
# DDS ainda leva o dela, então nó que vive menos de um segundo — o
# `ros2 service call`, por exemplo — simplesmente nunca é visto. O que incomoda
# na tela é o que dura o suficiente para ser desenhado e não o suficiente para
# ficar.
while true; do
    timeout 4 ros2 run demo_nodes_cpp parameter_blackboard >/dev/null 2>&1 || true
    sleep 3
done &

# Qualquer um deles caindo derruba o container.
wait -n
