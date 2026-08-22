# Debug durante a competição — o que a regra permite e o que falta construir

Alerta de escopo sobre o T1 Debug e especificação inicial de um segundo modo de
operação, para uso em partida.

- **Status:** nada implementado; este documento é o alerta e a lista de tarefas
- **Equipe:** RoboCIn
- **Fonte das regras:** [HSL-Rules](https://github.com/RoboCup-HumanoidSoccerLeague/HSL-Rules),
  `HEAD` em 2026-06-29 (`\RCYear = 2026`) — rascunho vivo, ver §7

---

## 1. O alerta

**O T1 Debug descrito em [`T1_DEBUG.md`](T1_DEBUG.md) é ferramenta de bancada e
box. Não pode ser usado durante uma partida.**

Isso não é defeito do desenho — é escopo. Mas é o tipo de suposição que alguém
do time faz errado às onze da noite antes de um jogo, então está escrito aqui.

A penalidade não é simbólica. `rules/game.tex:611`:

> Any team that exceeds the limits for wireless robot-to-robot communication
> defined in §robot-to-robot, as tracked by the GameController, will have all
> goals automatically nullified for the game.

---

## 2. O que a regra permite

Três canais, três orçamentos diferentes (`rules/robot_players.tex`, §Communication):

| Canal | Limite | Transporte |
|---|---|---|
| Robô ↔ robô (team messages) | **12000 mensagens por jogo**, 512 B cada | UDP **broadcast**, porta 10000 + nº do time |
| GameController → robôs | 2 pacotes/s | UDP |
| Robô → PC do time (**debug**) | **1 pacote/s por robô**, 1 destino | UDP, destino na rede cabeada |

O canal que interessa aqui é o terceiro, `rules/robot_players.tex:373`:

> Team specific debug information may be sent to an external computer owned by
> the team. Each robot may send debug information in a single UDP packet to a
> single team device on the wired network. The robot may send at most **1**
> packet(s) per second. The size of a single packet is not explicitly
> restricted, but must be within the maximum single UDP packet size
> (typically 64kB).

Detalhes do orçamento de 12000 que valem saber, mesmo não sendo deste canal: só
conta em `ready`, `set` e `playing`; +600 por minuto de *allowance for time
lost*; +6000 na prorrogação; não vale em disputa de pênaltis. O fonte avisa que
o limite **será reduzido** em competições futuras.

E o teto geral, `rules/robot_players.tex:286`: *"Any use of external power
supplies, teleoperation, remote control, remote processing, remote sensing, or
remote brain of any kind is prohibited."*

---

## 3. Por que o T1 Debug não cabe nessa janela

| A regra pede | O T1 Debug faz |
|---|---|
| UDP, um datagrama | TCP (SSH), fluxo contínuo |
| Unidirecional: robô **envia** | Bidirecional: a GUI escreve `assinar`/`desassinar` no `stdin` |
| 1 pacote/s | `grafo` a 1 Hz **mais** `msg` a até 10 Hz por tópico aberto (`ENVIO_MAX_HZ`, `src/agent.py:40`) |
| Só informação de debug saindo | Sobe e executa código no robô (`BOOTSTRAP`, `src/connection.py:44`) |

O ponto que mata não é a taxa — é a **direção**. A exceção da regra cobre o robô
*enviar* informação. Comando entrando no robô durante o jogo é exatamente o que
"teleoperation / remote control" proíbe, e o canal de comandos é o coração do
T1 Debug (§4 do `T1_DEBUG.md`).

Conclusão: não é ajuste de constante. É um segundo modo de operação.

---

## 4. Forma do modo competição

As restrições praticamente escrevem o desenho. Registrando a derivação, não só o
resultado:

**Unidirecional.** Sem canal de comando, o robô não pode ser perguntado o que
enviar. Logo a configuração — quais tópicos, quais campos — tem de ser decidida
**antes** do jogo: arquivo de config, parâmetro ROS ou variável de ambiente,
lida no boot. A GUI vira um receptor passivo.

**Um datagrama por segundo, sem retransmissão.** UDP não garante entrega e não
há orçamento para reenviar. Cada pacote precisa ser **autocontido**: snapshot
completo, nunca diff. Isso já é o princípio do §4 do `T1_DEBUG.md`, então a
decisão se mantém — aqui ela deixa de ser conveniência e vira obrigação.

**Numeração de sequência.** Sem ela não dá para distinguir "o robô travou" de
"o pacote se perdeu" — e essa é a pergunta que se faz no meio de um jogo.

**Cuidado com os 64 kB.** O teto teórico é ~65507 B, mas qualquer coisa acima do
MTU (~1500 B) fragmenta, e perder **um** fragmento perde o datagrama inteiro. A
rede cabeada do destino ajuda, o salto WiFi não. Recomendação: orçar ~8 kB por
pacote e tratar 64 kB como limite legal, não como alvo de engenharia.

**O que cabe em um pacote.** A 1 Hz não dá para mandar o grafo inteiro nem
mensagens cruas. O conteúdo tem de ser escolhido a dedo — estado da máquina de
estados, pose estimada, visão da bola, bateria, temperatura de junta, contadores
de erro. Isso é decisão de time, não de ferramenta.

**Reaproveitamento.** `converter()` e a truncagem de `src/agent.py:174` servem
igual aqui e não precisam ser reescritos.

---

## 5. Decisões em aberto

Responder **antes** de escrever código.

1. **O robô precisa estar cabeado?** A frase é *"a single team device on the
   wired network"* — gramaticalmente o "on the wired network" qualifica o
   **destino**, não o emissor, o que significa robô no WiFi do AP e PC no cabo.
   É a leitura natural e a que torna o modo viável, mas é ambígua o bastante
   para valer uma pergunta ao TC. **Se a leitura contrária valer, este modo não
   existe em jogo** e o resto deste documento vira ferramenta de aquecimento.
2. Pacotes de debug entram em algum orçamento contado pelo GameController, ou
   são realmente separados dos 12000?
3. "A single team device" — um destino por robô, ou um destino para o time
   inteiro? Muda se o receptor é um ou vários.
4. O limite de 1/s vale em todos os estados de jogo, ou só em
   `ready`/`set`/`playing` como o das team messages? O texto não diz.
5. Formato do payload: JSON (consistente com o protocolo atual, legível, gordo)
   ou binário empacotado (cabe muito mais, exige schema versionado)?

---

## 6. TODO

**Fase 0 — regra**
- [ ] Confirmar com o TC as cinco perguntas do §5, por escrito
- [ ] Reler o `Rules.pdf` publicado do ano da competição, não o `HEAD` do repo
- [ ] Confirmar a configuração de rede do evento (IPs do time, AP, ponto cabeado)

**Fase 1 — protocolo**
- [ ] Definir o conteúdo do snapshot com quem joga, não com quem programa
- [ ] Fixar o formato do payload e o orçamento de bytes por pacote
- [ ] Campo de versão desde o primeiro dia, como no protocolo atual (§4)
- [ ] Número de sequência + timestamp do robô

**Fase 2 — emissor no robô**
- [ ] Nó ROS 2 somente-leitura, 1 Hz, `sendto` para um destino fixo
- [ ] Config lida no boot; **nenhum** socket de entrada — auditável por leitura
- [ ] Reusar `converter()`/truncagem de `src/agent.py`
- [ ] Degradar sozinho: se o snapshot estourar o orçamento, cortar por prioridade

**Fase 3 — receptor no PC**
- [ ] Socket UDP + painel por robô, reaproveitando os widgets existentes
- [ ] Mostrar idade do último pacote e sequências perdidas por robô
- [ ] Vários robôs na mesma tela — é o caso de uso real em jogo

**Fase 4 — verificação**
- [ ] Contador de taxa que **prova** ≤1 pacote/s por robô, com log para o TC
- [ ] Teste com os 4 robôs simultâneos, em rede com perda induzida
- [ ] Ensaio completo no box antes do primeiro jogo

**Documentação**
- [ ] Marcar no `T1_DEBUG.md` §1 que aquele modo é bancada, com link para cá
- [ ] Corrigir o `T1_DEBUG.md` §1: o diagrama e o passo 3 ainda dizem
      "copia `agent.py` para `/tmp`"; o §5 e o código decidiram base64 na linha
      de comando, sem tocar o disco

---

## 7. Riscos

| Risco | Impacto | Mitigação |
|---|---|---|
| Usar o T1 Debug em partida por engano | Gols anulados | Este documento + aviso no `T1_DEBUG.md` |
| Regra muda entre o `HEAD` e o evento | Desenho inválido | Reconferir o PDF publicado antes de codar (Fase 0) |
| Leitura errada do "wired network" | Modo inteiro inviável | Perguntar ao TC antes da Fase 1 |
| Snapshot acima do MTU | Perda silenciosa de pacotes | Orçar ~8 kB, contar bytes antes de enviar |
| Bug faz o emissor passar de 1/s | Gols anulados | Trava de taxa no próprio emissor + contador auditável |
| Limite de 12000 cai em anos futuros | Retrabalho | Manter o snapshot enxuto desde já |
