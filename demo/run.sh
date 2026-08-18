#!/usr/bin/env bash
# Sobe (ou ressuscita) o robô de mentira e diz o que fazer em seguida.
set -euo pipefail

cd "$(dirname "$0")"

IMAGEM=t1-demo:humble
NOME=t1-demo
PORTA=2222

docker build -t "$IMAGEM" .

docker rm -f "$NOME" >/dev/null 2>&1 || true

# Porta publicada só em 127.0.0.1: a senha aqui é `robo`, e isso não pode ficar
# escutando na rede do laboratório.
docker run -d --name "$NOME" -p "127.0.0.1:$PORTA:22" "$IMAGEM" >/dev/null

printf '\n[demo] container `%s` de pé em 127.0.0.1:%s\n' "$NOME" "$PORTA"
printf '[demo] usuário: robo   senha: robo\n\n'

# A GUI usa StrictHostKeyChecking=yes por decisão (§6 do T1_DEBUG.md), então ela
# se recusa a conectar num host desconhecido. Registrar a host key uma vez é
# parte do teste, não um contorno.
if ! ssh-keygen -F "[127.0.0.1]:$PORTA" >/dev/null 2>&1; then
    cat <<AVISO
[demo] Este host ainda não está no seu ~/.ssh/known_hosts.
       A GUI vai falhar com "não está no known_hosts" — que é o comportamento
       correto. Registre a chave uma vez:

           ssh -p $PORTA robo@127.0.0.1

       (responda `yes`, senha `robo`, e saia com Ctrl-D)

AVISO
fi

cat <<PROXIMO
[demo] Na GUI:  SSH = robo@127.0.0.1:$PORTA   Senha = robo   Setup ROS = (vazio)

       O campo de setup fica vazio de propósito: assim o bootstrap tem que achar
       /opt/ros/humble/setup.bash sozinho, que é a camada 2 do §5.

[demo] Logs do robô falso:  docker logs -f $NOME
[demo] Derrubar:            docker rm -f $NOME
PROXIMO
