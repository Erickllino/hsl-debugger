# T1 Debug — inspetor de grafo ROS 2 para robôs humanoides

Ferramenta desktop para visualizar, em tempo real, os nós e tópicos ROS 2 rodando
num robô, sem instalar nada permanente no robô e sem instalar ROS 2 no PC.

- **Status:** arquitetura definida, implementação em andamento
- **Equipe:** RoboCIn
- **Alvo:** Booster T1 (ROS 2 Humble) e robôs equivalentes, inclusive emprestados

---

## 1. Ideia central

> A GUI abre uma sessão SSH, envia um script Python temporário para o robô,
> e conversa com ele pelo próprio canal do SSH.

O SSH não serve só para autenticar — ele **é o transporte dos dados**. O agente
escreve JSON no `stdout`, a GUI lê; a GUI escreve comandos no `stdin`, o agente lê.
Não existe porta TCP, túnel, nem processo escutando na rede.

```
[ GUI PySide6 no PC ]  ⇄  ssh  ⇄  [ agent.py em /tmp no robô ]  ⇄  DDS  ⇄  [ nós ROS 2 ]
        stdin/stdout                    processo efêmero
```

### Ciclo de vida

1. Usuário escolhe um perfil salvo e aperta **conectar**.
2. GUI abre a sessão SSH (chave ou senha em memória).
3. GUI copia `agent.py` para `/tmp` no robô.
4. GUI localiza e carrega o ambiente ROS do robô, depois executa o agente.
5. Agente entra no grafo DDS como nó somente-leitura e começa a emitir JSON.
6. Usuário fecha a janela → SSH cai → agente recebe EOF no `stdin` → morre.

Nada sobrevive à sessão.

---

## 2. Decisões e alternativas descartadas

Registro do raciocínio, para não refazer a discussão daqui a seis meses.

### 2.1 Por que não instalar ROS 2 no PC (DDS direto)

Tecnicamente funciona: o PC vira mais um participante DDS e enxerga o grafo inteiro,
sem código nenhum no robô. Descartado porque o custo é **uma instalação por pessoa**
— distros diferentes, versões diferentes, Docker, e alguém sempre com `ros2 node list`
vazio numa quinta à noite. Numa ferramenta de time, esse custo multiplica.

Mantido como possibilidade futura se aparecer requisito de latência baixa
(debug de malha de controle), mas não será construído sem esse requisito.

### 2.2 Por que não um daemon permanente no robô

Foi a recomendação anterior e caiu por uma restrição de uso: **o time usa robôs que
não são seus**. Deixar um daemon rodando 24/7 na máquina de outro laboratório não é
uma questão técnica, é uma questão de não ter esse direito. Junto com essa decisão
caíram: systemd, `lingering`, dúvida sobre sudo, e o fan-out multi-cliente.

### 2.3 Por que não uma porta TCP dedicada

Se o SSH já é o canal de autenticação, uma porta separada só adiciona problemas:
escolha de porta, colisão quando duas pessoas conectam no mesmo robô, firewall,
túnel manual, e um `bind` em `0.0.0.0` que exporia o grafo para a rede sem
autenticação nenhuma. `stdin`/`stdout` elimina todos de uma vez — e resolve o
processo órfão de graça, porque EOF mata o agente sem código adicional.

### 2.4 Por que não `subprocess` de `ros2 topic echo`

Ideia inicial do projeto, válida como MVP. Descartada porque a saída é YAML em
texto (parsing frágil, arrays grandes viram placeholder), cada tópico custa um
processo Python inteiro, e não há controle de QoS nem de frequência.

---

## 3. Restrições do agente

Estas são invariantes do projeto, não preferências.

**Somente leitura.** O agente assina tópicos e lê o grafo. Não publica, não chama
serviço, não escreve parâmetro, não executa ação. Sem `create_publisher`, sem
`create_client`. Isso é afirmável no README e defensável perante quem empresta o robô.

**Zero dependência externa.** Apenas biblioteca padrão do Python mais `rclpy`.
Nunca haverá `pip install` na máquina de terceiros.

**Arquivo único.** Um `.py` autocontido, copiável com um comando.

**Morre sozinho.** EOF no `stdin` encerra. Timeout de ociosidade também encerra.
Nenhum cenário deixa processo pendurado em robô emprestado.

**Custo de rede limitado.** Assinatura sob demanda (só o que está aberto na tela),
decimação para no máximo ~10 msg/s por tópico, e truncagem de payloads grandes.
As estatísticas de Hz contam todas as mensagens; só o envio é decimado.

**QoS casado com o publisher.** Antes de assinar, o agente lê o QoS anunciado e
cria a subscription compatível. Sem isso, tópicos de sensor (`BEST_EFFORT`) nunca
entregam nada e o usuário conclui que a ferramenta está quebrada.

---

## 4. Protocolo

JSON delimitado por `\n`, uma mensagem por linha, UTF-8.

**Campo de versão é obrigatório desde o primeiro dia.** Numa ferramenta de time,
alguém sempre estará com a GUI da semana passada; a GUI deve detectar
incompatibilidade e avisar em vez de falhar de forma obscura.

### GUI → agente

```json
{"cmd": "assinar",    "topico": "/joint_states"}
{"cmd": "desassinar", "topico": "/joint_states"}
{"cmd": "ping"}
```

### Agente → GUI

```json
{"v": 1, "tipo": "hello",  "ros_distro": "humble", "host": "t1-01"}
{"v": 1, "tipo": "grafo",  "nos": [...], "topicos": [...]}
{"v": 1, "tipo": "msg",    "topico": "/imu", "dados": {...}}
{"v": 1, "tipo": "status", "nivel": "erro", "msg": "..."}
```

O `grafo` é reenviado periodicamente (~1 Hz) com o snapshot completo. Snapshot
inteiro em vez de diff: mais bytes, muito menos estado para sincronizar dos dois lados.

**Cuidado com o `stdout`.** Qualquer `print` de debug no agente, ou qualquer
biblioteca que escreva em `stdout`, corrompe o canal. Logs do agente vão para
`stderr`, que a GUI captura separadamente e mostra num painel de diagnóstico.

---

## 5. O problema difícil: encontrar o ambiente ROS

Esta é a única parte realmente chata, e é onde o tempo de desenvolvimento vai.

SSH não-interativo **não** executa o `.bashrc`. O comando cai num shell sem ROS:
`import rclpy` falha, e as mensagens custom do SDK do Booster não estão no
`AMENT_PREFIX_PATH`. Em robô próprio você sabe o caminho; em emprestado, não.

Estratégia em camadas:

1. Caminho explícito salvo no perfil de conexão (sempre vence).
2. Procurar `/opt/ros/*/setup.bash` — pega a distro do sistema.
3. Procurar overlays de workspace em locais prováveis (`~/*_ws/install/setup.bash`).
4. Falhar com mensagem acionável — mostrar o que foi procurado e permitir que o
   usuário digite o caminho, salvando no perfil.

Overlay importa: sem ele, tópicos do SDK aparecem com tipo desconhecido e não
podem ser assinados. A GUI deve mostrar isso como aviso explícito, não como
tópico silenciosamente inútil.

**Envio do script.** Não é possível mandar o `.py` pelo `stdin` se o `stdin` é o
canal do protocolo. Duas opções, ambas de poucas linhas: copiar o arquivo numa
primeira invocação e executar numa segunda, ou embutir o script codificado na
própria linha de comando. Escolher uma e documentar.

---

## 6. Credenciais

**A GUI não persiste senha. Em nenhuma hipótese.**

Aceitar senha digitada e mantê-la em memória durante a sessão é aceitável. O que
não pode existir é a caixinha "lembrar senha": basta ela existir para que, em
poucos meses, haja senha de robô em texto plano no disco de várias pessoas do time
— inclusive senha de robô que não é do time.

Caminho recomendado, oferecido na primeira conexão: gerar par de chaves e copiar a
pública para o robô. São dois comandos, e depois disso ninguém digita senha nunca mais.

```bash
ssh-keygen -t ed25519
ssh-copy-id usuario@robo
```

Perfis de conexão salvos guardam host, usuário, porta, caminho do setup e apelido.
Nunca segredo.

---

## 7. Lado do PC

**Stack:** PySide6, biblioteca padrão. Nada além disso é obrigatório.
`pyqtgraph` entra só quando houver plot temporal.

**Threading.** O loop que lê o `stdout` do SSH roda numa `QThread`; comunicação com
a interface exclusivamente por `Signal`. Nenhum widget é tocado fora da thread da GUI.

**Distribuição.** O time clona o repositório, roda `pip install -e .` e executa um
comando. O host padrão vem do perfil — se a pessoa precisa descobrir e digitar IP
toda vez, o atrito que estamos evitando volta multiplicado pelo tamanho do time.

**Perfis são parte da interface.** Com dois robôs próprios mais emprestados
eventuais, "em qual máquina eu estou" é informação de primeira classe: visível no
título da janela e no rodapé, sempre.

---

## 8. Questão em aberto

**O que exatamente a janela mostra?** É a decisão mais cara de mudar depois, porque
determina a estrutura de dados que o agente produz. Três respostas possíveis, não
equivalentes:

- **Lista** — nós e tópicos em colunas, clique para inspecionar. Simples, cobre 80%
  do uso real, é o que o `rqt` faz mal.
- **Grafo** — quem publica para quem, desenhado. Bonito, caro de implementar,
  raramente é o que se precisa às 23h antes da competição.
- **Painel de saúde** — o que era esperado e não está chegando; nós que morreram;
  QoS incompatível. Mais opinativo, exige saber o que é o "normal" do robô, e
  provavelmente é o de maior valor real.

Decidir antes de escrever a interface.

---

## 9. Roadmap

**v0.1** — conectar por perfil, subir o agente, listar nós e tópicos com pubs/subs,
inspecionar um tópico, rodapé com estado da conexão e painel de `stderr`.

**v0.2** — Hz e banda por tópico, detecção e destaque de QoS incompatível,
leitura de parâmetros, console de `/rosout`.

**v0.3** — plot temporal de campos numéricos, árvore de TF com idade das transforms,
múltiplos painéis destacáveis (`QDockWidget`).

**Fora de escopo por ora** — backend DDS direto, escrita de qualquer tipo,
visualização 3D, replay de bags.

---

## 10. Riscos

| Risco | Impacto | Mitigação |
|---|---|---|
| Ambiente ROS não encontrado no robô | Não conecta | Busca em camadas + caminho manual no perfil (§5) |
| Overlay do SDK ausente | Tópicos custom inutilizáveis | Aviso explícito na interface |
| `print` acidental no agente | Protocolo corrompido | Logs sempre em `stderr` (§4) |
| Payload grande (imagem, nuvem) | Trava a sessão | Truncagem no agente (§3) |
| Senha persistida por conveniência | Vazamento de credencial | Proibido por design; migrar para chave (§6) |
| GUI e agente com versões diferentes | Falha obscura | Campo `v` no protocolo (§4) |
