# AutoClip Actions — Quality v9.6

O AutoClip processa um link do YouTube em GitHub Actions, escolhe histórias curtas com contexto e payoff, cria edição vertical 1080×1920, legendas, hook, capa automática, direção visual e uma revisão automática antes do upload. Quando autorizado, envia os vídeos ao Cloudinary e à fila do Buffer/TikTok.

O computador pessoal não precisa permanecer ligado depois que o workflow começa.

## Fluxo

YouTube → Whisper → análise do vídeo inteiro → perfil de conteúdo → duração inteligente → revisão do encerramento → Hook Guard → cenas/falantes → Active Speaker Lock → Camera Director → enquadramento → render → Auto Review → correção automática quando necessária → capa → Cloudinary → Buffer → TikTok.

## Quality v9.6 — Camera Director mais calmo

A v9.6 preserva a v9.5 e corrige três pontos percebidos em vídeos com muitas pessoas.

- **Falante ativo mais conservador** — o sistema analisa movimento da boca em vários frames próximos e desconta com mais força o movimento geral da cabeça. Um rosto grande ou central recebe apenas um bônus mínimo.
- **Troca de câmera sustentada** — um novo falante não provoca corte imediatamente. Normalmente ele precisa permanecer como candidato forte por vários samples antes de a câmera trocar. Pequenas incertezas mantêm o último falante confirmado.
- **Menos troca em interjeições curtas** — respostas rápidas ou ruído visual não obrigam a câmera a ir e voltar. A troca normal exige aproximadamente 2,7 s de estabilidade do enquadramento atual, salvo evidência excepcionalmente forte.
- **Micro punch-ins reduzidos** — mudanças muito curtas criadas apenas para combater estagnação visual são neutralizadas quando ficariam nervosas demais.
- **Enquadramento mais aberto** — o crop normal passa a preservar mais cabeça, ombros e contexto. O rosto ocupa uma fração menor do quadro e o Auto Review não transforma sua correção em um close exagerado.
- **Split-screen também um pouco mais aberto** — os dois painéis continuam centralizados nas pessoas corretas, mas com mais contexto em volta do rosto.

## Quality v9.5 — Hook Guard

- **Hook não se perde quando o final muda** — o Final Guard pode alterar o `end` do corte depois que o hook foi criado. A v9.5 reassocia o hook ao intervalo FINAL.
- **Revisão final do texto do hook** — quando o Gemini está disponível, os hooks finais são revisados depois que os cortes já estão definidos.
- **Texto mais natural** — 4–8 palavras, preferencialmente 5–7, compreensível para quem nunca viu o vídeo.
- **Sem clickbait genérico** — frases como `You won't believe`, `Watch until the end` e `This is crazy` são rejeitadas.
- **Fallback factual** — se o Gemini estiver indisponível, o AutoClip extrai uma frase curta do próprio clip.
- **Duração preservada** — padrão de 8 segundos e faixa de 1–15 segundos.

## Quality v9.4 — Active Speaker Lock

- **Prioridade para quem está falando** — em Podcast/Entrevista e Talking Head, o sistema compara atividade específica da região da boca.
- **Áudio + Whisper como gate** — movimento facial ganha peso forte somente quando há fala detectada naquele instante.
- **Active speaker locking** — a pessoa confirmada permanece em foco durante pequenos momentos ambíguos.
- **Melhor comportamento com 3+ pessoas** — os rostos são acompanhados como trilhas locais dentro do plano.
- **Split-screen protegido** — a pessoa superior tende a ser o falante e a segunda tela precisa ser outra pessoa distinta.

## Quality v9.3 — Final Guard + Auto Review

- **Proteção do final** — revisa se alguém foi cortado no meio da fala, se a ideia ficou incompleta ou se o clip avançou para outro assunto.
- **Correção do encerramento** — pode avançar até a conclusão da mesma ideia ou voltar ao fechamento anterior.
- **Auto Review do MP4** — revisa resolução, legendas, centralização dos rostos e consistência do split-screen.
- **Auto-correção visual** — problemas corrigíveis podem provocar uma segunda renderização automática.
- **Falha crítica bloqueia upload** — resolução incorreta ou legenda estruturalmente inválida impede o envio.
- **Legenda tamanho 70** — branca, inferior, uniforme e sem destaque automático.

## Split-screen e enquadramento

- **Split-screen adaptativo** — não possui duração fixa; começa e termina conforme a presença/relevância de duas pessoas distintas.
- **Duas identidades distintas** — falante e ouvinte são validados no mesmo frame.
- **Face-box + eye-line** — posição X/Y e tamanho do rosto são usados para calcular o crop.
- **Painéis independentes** — cada metade do split recebe enquadramento próprio.

O conjunto dos splits continua com orçamento total para não dominar o vídeo inteiro.

## Visual Director

- **Visual Attention Engine** — mede atividade visual e pode criar mudanças estáticas quando o plano fica parado demais; a v9.6 evita intervenções curtas demais.
- **Split-screen inteligente** — em Podcast/Entrevista, mostra falante + segunda pessoa quando as duas identidades são válidas e persistentes.
- **Reaction emphasis** — reações fortes ainda podem receber um close curto antes de voltar ao falante.

## Recursos preservados

- **Detecção de cenas** — respeita mudanças de câmera/cena do vídeo original.
- **Perfis** — `Auto`, `Podcast/Entrevista`, `Talking Head`, `Gameplay` e `Filme/Série`.
- **Duração inteligente** — trabalha entre 25 e 150 s e tenta usar o menor trecho que entregue contexto + desenvolvimento + payoff.
- **Loop natural** — quando existe, prefere um fim que reconecte bem ao começo sem duplicar falas.
- **Hook contextual** — factual, curto, resiliente ao Final Guard e com duração configurável.
- **Capa automática** — cria thumbnail 1080×1920 a partir de um frame forte do próprio clip.

## Controles no Run workflow

Permanecem:

- **Mostrar hook contextual no topo?** — ativado por padrão.
- **Duração do hook no topo** — padrão 8 segundos, faixa 1–15.
- **Visual Attention Engine**
- **Split-screen inteligente**
- **Reaction emphasis**
- **Auto Review** — ativado por padrão.

O Active Speaker Lock, Camera Director e Hook Guard são automáticos nos perfis suportados.

## Como testar

1. Abra **Actions → AutoClip - Processar vídeo → Run workflow**.
2. Cole o link do YouTube.
3. Confirme que possui direito/permissão para reutilizar o conteúdo.
4. Para avaliar a v9.6, use `1` corte, Whisper `base`, legenda `English` e perfil `Podcast/Entrevista`.
5. Escolha de preferência um vídeo com 3 ou mais pessoas no mesmo enquadramento e trocas de falante.
6. Deixe hook, Visual Attention, Split-screen, Reaction emphasis e Auto Review ativados.
7. Mantenha a capa ativada.
8. Deixe **Enviar ao Buffer/TikTok** desmarcado durante o teste.
9. Execute o workflow.

Observe principalmente: se a câmera permanece mais tempo no falante confirmado, se evita focar pessoas inativas, se as trocas deixaram de parecer nervosas e se o enquadramento ficou menos fechado.

## Secrets obrigatórios

Em **Settings → Secrets and variables → Actions → Repository secrets**:

- `CLOUDINARY_CLOUD_NAME`
- `CLOUDINARY_API_KEY`
- `CLOUDINARY_API_SECRET`
- `BUFFER_API_KEY`
- `YOUTUBE_COOKIES_B64`

Opcionais: `BUFFER_CHANNEL_ID` e `GEMINI_API_KEY`.

Repository variables opcionais: `GEMINI_MODEL` e `CLOUDINARY_FOLDER`.

## Segurança

Nunca coloque API keys ou cookies diretamente no código. Use GitHub Actions Secrets.

## Direitos autorais

Use o fluxo somente com material próprio, licenciado ou que você tenha autorização para reutilizar. Cortar, legendar, reenquadrar, criar capa ou adicionar efeitos não remove os direitos autorais do conteúdo original.
