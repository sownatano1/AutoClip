# AutoClip Actions — Quality v9.4

O AutoClip processa um link do YouTube em GitHub Actions, escolhe histórias curtas com contexto e payoff, cria edição vertical 1080×1920, legendas, hook, capa automática, direção visual e uma revisão automática antes do upload. Quando autorizado, envia os vídeos ao Cloudinary e à fila do Buffer/TikTok.

O computador pessoal não precisa permanecer ligado depois que o workflow começa.

## Fluxo

YouTube → Whisper → análise do vídeo inteiro → perfil de conteúdo → duração inteligente → revisão do encerramento → cenas/falantes → Active Speaker Lock → Visual Director → enquadramento por rosto → render → Auto Review → correção automática quando necessária → capa → Cloudinary → Buffer → TikTok.

## Quality v9.4 — Active Speaker Lock

A v9.4 preserva a v9.3 e melhora principalmente clips com várias pessoas no mesmo enquadramento.

- **Prioridade real para quem está falando** — durante Podcast/Entrevista e Talking Head, o sistema amostra o plano várias vezes por segundo e compara a atividade específica da região da boca de cada rosto.
- **Áudio + Whisper como gate** — movimento facial só conta fortemente quando há fala detectada naquele instante. A energia do áudio ajuda a evitar que uma pessoa gesticulando ou mexendo a cabeça seja confundida com o falante.
- **Tamanho do rosto não domina mais** — uma pessoa grande ou central no quadro recebe apenas um bônus mínimo. O movimento labial durante fala passa a ser o principal sinal.
- **Active speaker locking** — depois que um falante é identificado, o enquadramento permanece nele durante pequenos momentos ambíguos. A câmera só troca para outra pessoa quando a evidência persiste por vários frames.
- **Troca sustentada de falante** — se outra pessoa começa realmente a falar, o mesmo plano pode ser subdividido e o crop passa para ela sem esperar um corte artificial longo.
- **Melhor comportamento com 3+ pessoas** — os rostos são acompanhados como trilhas locais dentro do plano; o sistema compara as pessoas visíveis em vez de manter o foco na posição escolhida anteriormente.
- **Split-screen protegido** — quando um split coincide com fala ativa, a pessoa superior é corrigida para o falante detectado. A segunda tela precisa ser outra pessoa distinta e persistente; se isso não puder ser comprovado, o sistema abandona o split e mostra apenas o falante.

## Quality v9.3 — Final Guard + Auto Review

- **Proteção do final** — revisa se alguém foi cortado no meio da fala, se a ideia ficou incompleta ou se o clip avançou para o começo de outro assunto.
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

- **Visual Attention Engine** — mede atividade visual e pode criar um punch-in estático quando o plano fica parado demais.
- **Split-screen inteligente** — em Podcast/Entrevista, mostra falante + segunda pessoa quando as duas identidades são válidas e persistentes.
- **Reaction emphasis** — reações fortes podem receber um close curto antes de voltar ao falante.

## Recursos preservados

- **Detecção de cenas** — respeita mudanças de câmera/cena do vídeo original.
- **Perfis** — `Auto`, `Podcast/Entrevista`, `Talking Head`, `Gameplay` e `Filme/Série`.
- **Duração inteligente** — trabalha entre 25 e 150 s e tenta usar o menor trecho que entregue contexto + desenvolvimento + payoff.
- **Loop natural** — quando existe, prefere um fim que reconecte bem ao começo sem duplicar falas.
- **Hook contextual** — factual, curto, opcional e com duração configurável.
- **Capa automática** — cria thumbnail 1080×1920 a partir de um frame forte do próprio clip.

## Controles no Run workflow

Permanecem:

- **Visual Attention Engine**
- **Split-screen inteligente**
- **Reaction emphasis**
- **Auto Review** — ativado por padrão; revisa e tenta corrigir o clip antes do upload.

O Active Speaker Lock da v9.4 é automático nos perfis Podcast/Entrevista e Talking Head.

## Como testar

1. Abra **Actions → AutoClip - Processar vídeo → Run workflow**.
2. Cole o link do YouTube.
3. Confirme que possui direito/permissão para reutilizar o conteúdo.
4. Para avaliar a v9.4, use `1` corte, Whisper `base`, legenda `English` e perfil `Podcast/Entrevista`.
5. Escolha de preferência um vídeo com 3 ou mais pessoas aparecendo juntas e troca frequente de falante.
6. Deixe Visual Attention, Split-screen, Reaction emphasis e **Auto Review** ativados.
7. Mantenha hook e capa ativados.
8. Deixe **Enviar ao Buffer/TikTok** desmarcado durante o teste.
9. Execute o workflow.

O Job Summary preserva as informações do Auto Review e também registra a camada Active Speaker v9.4.

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
