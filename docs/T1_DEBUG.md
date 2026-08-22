# T1 Debug — inspetor de grafo ROS 2 para robôs humanoides

Ferramenta desktop para visualizar, em tempo real, os nós e tópicos ROS 2 rodando
num robô, sem instalar nada permanente no robô e sem instalar ROS 2 no PC.

- **Status:** arquitetura definida, implementação em andamento
- **Equipe:** RoboCIn
- **Alvo:** Booster T1 (ROS 2 Humble) e robôs equivalentes, inclusive emprestados
- **Uso:** bancada e box. **Não pode ser usado durante uma partida** — ver
  [`competition_debug.md`](competition_debug.md)

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

1. **Lista** de caminhos explícitos salva no perfil de conexão (sempre vence).
2. Procurar `/opt/ros/*/setup.bash` — pega a distro do sistema.
3. Procurar overlays de workspace em locais prováveis (`~/*_ws/install/setup.bash`).
4. Falhar com mensagem acionável — mostrar o que foi procurado e permitir que o
   usuário digite os caminhos, salvando no perfil.

**A camada 1 é uma lista, não um caminho.** A distro do sistema e o workspace
com as mensagens do SDK são dois `setup.bash` diferentes, e uma fonte só
obrigaria a escolher entre ter ROS e ter as mensagens. As fontes são carregadas
**na ordem da lista** — underlay primeiro, overlay depois, que é a ordem que o
`AMENT_PREFIX_PATH` respeita. Linha vazia e linha começando com `#` são
ignoradas, então a lista aguenta ser um bloco anotado. `~/` é expandido pelo
bootstrap, porque dentro de variável o shell não expande.

**Mas a lista não é montada no login.** O formulário tem um campo *Setup ROS* de
uma linha, e mais nada: na hora de logar ninguém sabe ainda que vai faltar uma
segunda fonte. Isso se descobre depois, olhando a lista de tópicos e vendo um
com tipo não carregado. Então acrescentar fonte é ação do rodapé — o botão
**+ setup ROS**, que só existe conectado, e cujo diálogo já vem com os
`overlay disponível, não carregado` que o bootstrap relatou.

Acrescentar fonte **reabre a sessão**. Não é limitação da interface: o bootstrap
carrega o ambiente antes do `python3` existir, e `AMENT_PREFIX_PATH`/`PYTHONPATH`
são lidos no import — não há como sourcear um `setup.bash` dentro de um agente
que já está rodando. Derrubar e subir de novo é barato porque o agente é efêmero
por construção (§3); o que se perde são os tópicos abertos, não estado no robô.
A senha continua em memória durante a reabertura, o que o §6 permite
explicitamente — sem isso, cada fonte acrescentada custaria digitar a senha de novo.

O histórico guarda a lista inteira, inclusive o que foi acrescentado no meio da
sessão. Ao recarregar um perfil, a primeira fonte volta para o campo e as demais
voltam como extras — o campo encolheu, a memória não.

**O campo já vem preenchido** com `/hsl-player/install/setup.bash` — o workspace
do time, e o caminho certo na esmagadora maioria das conexões (`SETUP_PADRAO`,
`src/connection.py`). Um campo que já vem certo é a diferença entre "funciona" e
"descobrir por que o tópico está laranja". Não é imposição: perfil salvo vence o
padrão — inclusive quando o que funcionou naquele robô foi o campo **vazio**, já
que sobrescrever isso desfaria a descoberta da última vez — e apagar a linha
volta ao automático.

Três regras de precedência, todas consequência de "a lista é a receita":

- a camada 2 (`/opt/ros/*`) só entra se, depois da lista, `ROS_DISTRO` continuar
  vazio. Um `install/setup.bash` de colcon já carrega o underlay dele junto;
  sourcear `/opt/ros` por cima inverteria a precedência;
- a camada 3 (`~/*_ws`) **não** é carregada quando a lista existe — seria
  overlay entrando por cima do que o usuário escolheu. Os candidatos encontrados
  vão para o diagnóstico como `overlay disponível, não carregado`, para ele
  decidir acrescentar;
- mas "a lista existe" quer dizer **alguma fonte da lista carregou**, não que o
  campo tinha texto. A distinção só passou a importar quando o campo ganhou um
  padrão: num robô que não tem `/hsl-player`, a lista inteira falha, e aí a
  situação é idêntica à de campo vazio — a busca automática tem que valer. Sem
  isso, um padrão errado desligaria a descoberta para todo mundo.

Overlay importa: sem ele, tópicos do SDK aparecem com tipo que não importa e não
podem ser assinados. A GUI mostra isso como aviso explícito, não como tópico
silenciosamente inútil, e em dois lugares:

- **na lista de tópicos**, a coluna *Tipo* vem pintada quando o tipo está no
  grafo mas o pacote da mensagem não existe no ambiente carregado. O agente
  testa o `import` de cada tipo uma vez, no snapshot (`legivel`), justamente
  para a resposta aparecer na lista inteira de uma vez em vez de tópico por
  tópico, ao clicar;
- **na contagem do topo**, como `N sem tipo carregado`. É a resposta imediata à
  pergunta "meu setup pegou as mensagens do SDK?".

**Envio do script.** Não é possível mandar o `.py` pelo `stdin` se o `stdin` é o
canal do protocolo. Duas opções, ambas de poucas linhas: copiar o arquivo numa
primeira invocação e executar numa segunda, ou embutir o script codificado na
própria linha de comando.

**Decidido: embutir em base64 na linha de comando.** Uma invocação só, e nada é
escrito no disco do robô — o que fecha o passo 3 do §1 sem deixar rastro em
máquina emprestada e sem etapa de limpeza. O `agent.py` atual dá ~9 KB de linha
de comando, longe do `ARG_MAX` (~2 MB). Se um dia o agente crescer demais, a
alternativa do `scp` continua válida.

O truque que faz isso funcionar é usar substituição de comando em vez de pipe,
nos dois níveis:

```sh
ssh -T usuario@robo 'bash -c "$(printf %s <BOOTSTRAP_B64> | base64 -d)"'
# e, dentro do bootstrap, depois de carregar o ambiente:
exec python3 -c "$(printf %s "$agent_b64" | base64 -d)"
```

Com pipe (`... | bash`), o `stdin` do shell — e por herança o do agente — seria
o pipe já consumido, e o canal do protocolo morreria antes de existir.

**Transporte no PC: `ssh` do sistema via `QProcess`**, não biblioteca SSH em
Python. Mantém o §7 (só PySide6 e biblioteca padrão) e herda `~/.ssh/config`,
`known_hosts` e agente de chaves do usuário de graça.

---

## 6. Credenciais

**A GUI não persiste senha. Em nenhuma hipótese.**

Aceitar senha digitada e mantê-la em memória durante a sessão é aceitável. O que
não pode existir é a caixinha "lembrar senha": basta ela existir para que, em
poucos meses, haja senha de robô em texto plano no disco de várias pessoas do time
— inclusive senha de robô que não é do time.

Quando há senha digitada, ela vai ao `ssh` por `SSH_ASKPASS` — um helper `0700`
temporário que lê a senha de variável de ambiente do processo filho. Nunca por
`sshpass -p`, que deixaria a senha visível no `ps` para qualquer usuário do PC.
O campo é limpo assim que a sessão sobe.

Com senha em jogo, `StrictHostKeyChecking=yes` é obrigatório: no modo `ask`, a
pergunta de host key também passaria pelo askpass e seria respondida com a
senha. Host desconhecido falha com instrução em vez de perguntar.

Caminho recomendado, oferecido na primeira conexão: gerar par de chaves e copiar a
pública para o robô. São dois comandos, e depois disso ninguém digita senha nunca mais.

```bash
ssh-keygen -t ed25519
ssh-copy-id usuario@robo
```

Perfis de conexão salvos guardam host, usuário, porta, caminho do setup e apelido.
Nunca segredo.

O histórico de endereços vive em `~/.config/t1-debug/hosts.json` — texto simples,
para ser lido e editado à mão. Cada registro tem `alvo`, `setup` e `ultimo`, e a
leitura descarta qualquer outra chave: se alguém acrescentar `"senha"` ao arquivo,
o carregamento joga fora em vez de usar. Só endereço que **conectou** entra, senão
a lista vira um arquivo dos seus próprios erros de digitação.

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

## 8. O que a janela mostra — lista, e agora o desenho

**Decidido: lista primeiro.** Duas tabelas — nós e tópicos — com inspeção do
tópico selecionado embaixo. O grafo desenhado entrou depois, como planejado:
segunda leitura da **mesma** estrutura de dados, numa aba ao lado das tabelas
(`src/desenho.py`). O agente não ganhou uma linha para isso existir — ele já
emitia nome completo de nó (`namespace` + nome) em `nos`, `pubs` e `subs`, que é
o que uma aresta precisa. O painel de saúde continua sendo o de maior valor, mas
ele exige saber o que é o "normal" do robô — e isso não se descobre sem robô na
mão.

### Por que não `rqt_graph`

Ele existe, faz exatamente esse desenho e usa graphviz. Não serve aqui porque
roda **onde o DDS está** — no robô — e a janela só chegaria ao PC por `ssh -X`.
Três coisas quebram nesse caminho: `rqt` vem no `ros-*-desktop` e robô costuma
ter `ros-base`; instalar num robô emprestado é o que o §2.2 proíbe; e X11 sobre
wifi é lento. O próprio container de `demo/` mostra o sintoma barato: tem
`X11Forwarding yes` e **não** tem `xauth`, então o encaminhamento falha calado.
Desenhar no PC, a partir do JSON que já chega, não depende de nada disso.

### Decisões do desenho

- **Bipartido** (nó → tópico → nó). Ligar publicador direto no assinante
  esconderia por qual tópico eles conversam, que é justamente a pergunta.
- **O desenho começa mostrando tudo**, e ctrl+clique apaga qualquer caixa;
  *reorganizar* devolve. `/rosout` e `/parameter_events` são quase sempre os
  dois primeiros a apagar — todo nó publica neles e o desenho vira novelo — mas
  escondê-los por padrão seria a ferramenta decidindo por você o que você pode
  ver, antes de você ter visto. Apagar é maquiagem de tela: não muda a tabela,
  não muda o grafo, não chega no robô.
- **Ciclo é o caso normal** (controlador → comando → planta → estado →
  controlador), então o layout não supõe DAG: a aresta de retorno é detectada,
  sai do cálculo das camadas e é desenhada tracejada, em arco por baixo.
- **Acima de 300 caixas o desenho se recusa** e pede um filtro, em vez de
  entregar borrão e gastar CPU a cada mudança do grafo.
- **Animação de fluxo só no tópico aberto.** ROS 2 não informa taxa de
  publicação sem assinar, e assinar tudo para poder animar tudo violaria o §3.
  Os pontos correm na velocidade do Hz **medido** (escala logarítmica, porque a
  faixa real vai de 0,1 a 500 Hz), e o Hz conta todas as mensagens, não as que
  sobraram da decimação. Fio parado quer dizer "não estou medindo isto", nunca
  "não passa nada aqui".

### Serviço não é nó, mas aparece na lista de nós

O grafo do ROS 2 não distingue "nó que move o robô" de "nó que só existe para
servir ou chamar um serviço" — `get_node_names_and_namespaces()` devolve os dois
misturados. O segundo tipo costuma ser efêmero: um cliente nasce para uma
chamada e morre depois, e num snapshot a 1 Hz ele entra e sai da lista. O
resultado é uma tabela que pisca, e o que pisca atrapalha ler o que não pisca.

Quem classifica é o agente, não a GUI: `perfil()` pergunta ao grafo do robô
quantos tópicos, serviços e clientes **próprios** cada nó tem. "Próprio" desconta
o que todo nó ROS 2 ganha de graça — `/rosout`, `/parameter_events` e os seis
serviços de parâmetro; sem esse desconto todo nó pareceria ter tópico e serviço,
e a classificação não separaria nada. Nó com zero tópico próprio é nó de
serviço.

A caixa **ocultar serviços** vem marcada, e isso é uma exceção deliberada à
regra do desenho logo acima. A diferença é que `/rosout` fica parado na tela e
só polui; nó de serviço efêmero **muda a tela a cada segundo**, e o custo dele
não é ocupar espaço, é atrapalhar a leitura do que você veio ver. A exceção só
se sustenta porque nada some calado: a contagem do topo sempre diz `N serviços
ocultos`, o tooltip lista os nomes um por um, e desmarcar a caixa traz todos de
volta na hora.

Dois detalhes que não são opcionais:

- o nó do **próprio agente** também não tem tópico e cairia na mesma peneira.
  Ele vem marcado no snapshot e nunca é escondido — "em qual máquina eu estou" é
  informação de primeira classe (§7), e o agente sumindo da lista responde essa
  pergunta errado;
- esconder o nó não basta: ele publica em `/rosout` como qualquer um, e deixar o
  nome dele nos `pubs` faria a contagem de `/rosout` oscilar do mesmo jeito. Com
  o filtro ligado, os nós escondidos saem também dos `pubs`/`subs` dos tópicos —
  é isso que faz a tabela parar de ser reconstruída a cada aparição.

Nó que o agente **não conseguiu** classificar (sumiu entre a listagem e a
consulta) vem sem o campo e fica visível. Na dúvida, mostrar.

Registro das três respostas possíveis, que não eram equivalentes:

- **Lista** — nós e tópicos em colunas, clique para inspecionar. Simples, cobre 80%
  do uso real, é o que o `rqt` faz mal.
- **Grafo** — quem publica para quem, desenhado. Bonito, caro de implementar,
  raramente é o que se precisa às 23h antes da competição.
- **Painel de saúde** — o que era esperado e não está chegando; nós que morreram;
  QoS incompatível. Mais opinativo, exige saber o que é o "normal" do robô, e
  provavelmente é o de maior valor real.

### Ambiente de desenvolvimento

Sem robô na mão, o alvo é o container de `demo/` — `sshd` de verdade, ROS 2
Humble e nós publicando, com o usuário sem ROS no `.bashrc` para que o §5 seja
exercitado em vez de contornado. Ver [`demo/README.md`](../demo/README.md).

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
