# Robô de mentira

Um container que se comporta como o robô do ponto de vista da GUI: tem `sshd`,
tem ROS 2 Humble, tem nós publicando, e **não** tem ROS no `.bashrc` do usuário.

```bash
./demo/run.sh
```

Depois, na GUI:

| Campo | Valor |
|---|---|
| SSH | `robo@127.0.0.1:2222` |
| Senha | `robo` |
| Setup ROS | depende do que você quer testar — ver abaixo |

## Onde mora cada arquivo

Vale dizer explicitamente, porque a pergunta aparece: **não existe `setup.bash`
no repositório.** Ele é gerado pelo `colcon` durante o `docker build` e só passa
a existir dentro da imagem.

| No repositório | Vira, dentro do container |
|---|---|
| `demo/Dockerfile` | a receita da imagem |
| `demo/t1_robo_msgs/` (o `.msg` e o `package.xml`) | `/hsl-player/src/`, e o build produz **`/hsl-player/install/setup.bash`** |
| `demo/demo-nodes.sh` | `/usr/local/bin/demo-nodes.sh`, o que publica os tópicos |
| `demo/entrypoint.sh` | `/usr/local/bin/entrypoint.sh`, o PID 1 |

Para olhar de fora:

```bash
docker exec t1-demo ls /hsl-player/install     # o setup.bash está aqui
docker exec -it t1-demo bash                    # entrar e fuçar
```

## Testando o campo *Setup ROS*

O tópico `/sensor` usa `t1_robo_msgs/msg/Sensor`, que não existe em `/opt/ros`.
O workspace dela fica em `/hsl-player` de propósito: o bootstrap varre
`~/*_ws` sozinho (§5, camada 3) e **não** varre `/opt`, então informar o caminho
é a única forma de carregar a mensagem. É o ensaio do problema das mensagens do
SDK do Booster — tipo presente no grafo, pacote ausente no ambiente.

O caminho é o mesmo do robô de verdade, e a GUI já abre com ele preenchido
(`SETUP_PADRAO`, em `src/connection.py`). Então o caso normal e o caso quebrado
são estes:

**Como o campo já vem** (`/hsl-player/install/setup.bash`) — `/sensor` legível e
assinável. Só essa linha basta: um `install/setup.bash` de colcon carrega o
underlay `/opt/ros/humble` junto, que é por que a camada 2 não precisa entrar.

**Apagando o campo** — o bootstrap vasculha e só acha `/opt/ros/humble`.
`/sensor` aparece com o tipo pintado de laranja, o inspetor recusa a assinatura
explicando que falta o overlay, e a contagem do topo diz `1 sem tipo carregado`.

É o segundo caso que vale rodar, porque é o do robô emprestado: você conecta sem
saber o caminho, vê o tópico laranja, e resolve pelo botão **+ setup ROS** do
rodapé sem precisar deslogar.

## Por que não usar só o `ssh` falso do Passo 8

O script de `/tmp/fakebin/ssh` testa a montagem do comando, o base64 e o
protocolo — mas ele não tem autenticação, não tem `known_hosts`, não tem shell
não-interativo, e o grafo que ele produz tem um nó só: o próprio agente. Serve
para o passo 8; não serve para desenvolver uma tela que lista nós e tópicos.

O container exercita o caminho inteiro, incluindo as três coisas que quebram em
campo:

1. **autenticação** — senha via `SSH_ASKPASS`, o caminho do §6;
2. **`known_hosts`** — a GUI usa `StrictHostKeyChecking=yes` e se recusa a
   conectar num host desconhecido. Na primeira vez você registra a chave à mão,
   e isso é um ensaio honesto da mensagem de erro;
3. **shell sem ROS** — o `sshd` não roda o `.bashrc`, então o bootstrap do §5
   tem que achar o `/opt/ros/humble/setup.bash` sozinho. Deixe o campo *Setup
   ROS* vazio justamente para testar isso.

## Sobre a imagem base

Vem de `hl-unification:humble`, que já está na máquina e já traz ROS Humble,
`rclpy` e os `demo_nodes`. A única camada nova é o `openssh-server`. Se essa
imagem sumir, qualquer `ros:humble` oficial serve — troque a linha `FROM`.

As host keys são geradas **na build**, não no `run`. Assim recriar o container
não invalida a entrada do seu `~/.ssh/known_hosts`; só um rebuild da imagem
invalida. Quando isso acontecer:

```bash
ssh-keygen -R "[127.0.0.1]:2222"
```

## Segurança

A senha é `robo` e é fraca de propósito. Por isso o `run.sh` publica a porta em
`127.0.0.1:2222`, nunca em `0.0.0.0` — este container não deve ficar acessível
na rede do laboratório. Se preferir chave em vez de senha, o `sshd` é de
verdade e o caminho normal funciona:

```bash
ssh-copy-id -p 2222 robo@127.0.0.1
```

## O grafo atual

| O que roda | Para exercitar o quê |
|---|---|
| `talker` + `listener` em `/chatter` | o caso normal |
| `ros2 topic pub` em `/sensor` | mensagem custom, que só importa com `/hsl-player` informado no campo |
| `add_two_ints_server` | nó de serviço estável — sem tópico próprio, escondido pela caixa *ocultar serviços* |
| `parameter_blackboard` num laço de 4 s | nó de serviço que **pisca**: é ele que fazia a lista de nós se reconstruir a cada segundo |

O `parameter_blackboard` vive 4 s e não meio segundo de propósito. O snapshot vai
a 1 Hz e a descoberta do DDS leva o tempo dela, então nó que vive menos de um
segundo — um `ros2 service call`, por exemplo — simplesmente nunca chega a ser
visto. O que incomoda na tela é o que dura o suficiente para ser desenhado e não
o suficiente para ficar.

Ainda falta o grafo pesado: `/joint_states`, IMU em `BEST_EFFORT` e um tópico
grande para testar truncagem.

## Comandos

```bash
docker logs -f t1-demo    # o que o robô falso está fazendo
docker rm -f t1-demo      # derrubar
```
