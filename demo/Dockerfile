# Robô de mentira para desenvolver o T1 Debug sem robô.
#
# A base já é uma imagem de ROS 2 Humble que existe na máquina (mesma família
# das que o time usa), então ela já tem /opt/ros/humble, rclpy e os demo_nodes.
# A única coisa que falta para ela virar um alvo de SSH é o sshd.
#
# O ponto de todo este arquivo é NÃO facilitar a vida da GUI: o usuário `robo`
# não tem ROS no .bashrc, exatamente como num robô de verdade. É isso que faz o
# bootstrap do §5 ser testado para valer em vez de encontrar o ambiente pronto.
FROM hl-unification:humble

RUN apt-get update \
    && apt-get install -y --no-install-recommends openssh-server \
    && rm -rf /var/lib/apt/lists/*

# Host keys geradas na build, não a cada `docker run`: assim o container pode
# ser recriado sem que a entrada do ~/.ssh/known_hosts do seu PC vire conflito.
# Só um rebuild da imagem troca a chave — e aí o run.sh avisa.
RUN mkdir -p /run/sshd && ssh-keygen -A

# Senha fraca de propósito e só para localhost. O run.sh publica a porta em
# 127.0.0.1, nunca em 0.0.0.0.
RUN useradd --create-home --shell /bin/bash robo \
    && echo 'robo:robo' | chpasswd

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
COPY demo-nodes.sh /usr/local/bin/demo-nodes.sh
RUN chmod +x /usr/local/bin/entrypoint.sh /usr/local/bin/demo-nodes.sh

EXPOSE 22
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
