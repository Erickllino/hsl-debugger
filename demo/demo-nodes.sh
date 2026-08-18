#!/bin/bash
# Grafo mínimo: só o suficiente para a GUI ter o que listar.
#
# Versão básica de propósito — talker e listener trocando /chatter. O grafo rico
# (joint_states, IMU em BEST_EFFORT, tópico pesado, nó que morre) entra depois,
# quando a lista de nós e tópicos já estiver na tela.
set -e

source /opt/ros/humble/setup.bash

echo "[demo] ROS_DISTRO=$ROS_DISTRO ROS_DOMAIN_ID=${ROS_DOMAIN_ID:-0}"

ros2 run demo_nodes_cpp talker &
ros2 run demo_nodes_cpp listener &

# Qualquer um dos dois caindo derruba o container.
wait -n
