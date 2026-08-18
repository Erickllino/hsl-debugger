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
| Setup ROS | *(vazio)* |

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

Mínimo de propósito: `talker` e `listener` trocando `/chatter`.

O grafo rico — `/joint_states`, IMU em `BEST_EFFORT`, um tópico pesado para
testar truncagem e um nó que morre sozinho para ver o grafo encolher — entra
depois, em `demo-nodes.sh`, quando a lista de nós e tópicos estiver de pé.

## Comandos

```bash
docker logs -f t1-demo    # o que o robô falso está fazendo
docker rm -f t1-demo      # derrubar
```
