# AutoClip Actions — Quality v9.9

O AutoClip processa um link do YouTube em GitHub Actions, entende o contexto do vídeo antes de escolher os cortes, cria edição vertical 1080×1920, legendas, hook, capa, direção visual, revisão automática e uma legenda social contextual para Buffer/TikTok.

O computador pessoal não precisa permanecer ligado depois que o workflow começa.

## Fluxo

YouTube metadata/legendas → download → Whisper → **Source Intelligence** → seleção editorial → duração inteligente → Final Guard → Hook Guard → cenas/falantes → Active Speaker Lock → Camera Director → enquadramento → render → Auto Review → capa → **Social Caption Guard** → Cloudinary → Buffer → TikTok.

## Quality v9.9 — Social Caption Guard

A v9.9 corrige a geração de legenda social e hashtags, inclusive quando o Gemini está sem quota.

- **Caption como título/hook do clipe** — a primeira linha descreve o que o espectador está prestes a assistir, em vez de simplesmente copiar a primeira fala da transcrição.
- **Rejeita caption que parece legenda falada** — cópia literal ou quase literal do diálogo é descartada e substituída por uma formulação editorial.
- **Contexto semântico** — usa título, descrição, tags, canal, Source Intelligence, mapa de assuntos e diálogo do corte.
- **Fallback local inteligente** — se o Gemini retornar `429`, `503` ou estiver ausente, o sistema ainda cria uma caption usando o formato do vídeo e o contexto real.
- **6–10 hashtags úteis** — a v9.9 prefere poucas tags fortes a uma lista longa preenchida com palavras aleatórias.
- **Filtro duro de palavras vazias** — pronome, contração, artigo, verbo comum e tokens como `#Its`, `#Thats`, `#Im`, `#Say`, `#Think`, `#One` e equivalentes são rejeitados.
- **Sem enchimento artificial** — `#viral`, `#fyp`, `#tiktok` e `#clips` não entram apenas para completar quantidade.
- **Entidades e assuntos reais** — prioriza pessoa, franquia, filme/série/game, personagem, assunto e formato somente quando sustentados pelo metadata ou pelo próprio corte.
- **Validação também para a IA** — mesmo hashtags sugeridas pelo Gemini passam pelo filtro semântico antes de chegar ao Buffer.

Exemplo para um trecho de um Pop Quiz do elenco de Spider-Man em que precisam completar uma frase conhecida:

`Can the Spider-Man Cast Finish This Iconic Quote?`

Tags possíveis, quando sustentadas pelo vídeo/corte:

`#SpiderMan #BrandNewDay #Marvel #MCU #Hulk #PopQuiz #Trivia #GQ`

## Quality v9.8 — Source Intelligence

A v9.8 adicionou uma etapa editorial **antes da escolha dos cortes**.

- **Lê o título do YouTube** — o título deixa de ser apenas referência da fonte e passa a fazer parte do contexto editorial.
- **Lê descrição, canal/uploader, tags, categorias e capítulos** — quando esses dados existem, ajudam o AutoClip a entender formato, tema, participantes e estrutura do vídeo.
- **Tenta ler a legenda nativa do YouTube** — prioriza legenda manual e depois automática, usando formatos `json3` ou `vtt` quando disponíveis.
- **Fallback seguro para Whisper** — se a legenda nativa estiver ausente, protegida ou não puder ser baixada, o processamento continua usando a transcrição completa do Whisper.
- **Analisa a linha do tempo inteira** — a transcrição é compactada preservando começo, meio e fim.
- **Mapa de assuntos** — procura blocos de assunto, mudanças de tema, histórias/perguntas completas e possíveis transições.
- **Contexto global + contexto local** — diferencia o assunto geral do vídeo da ideia específica de cada momento.
- **Participantes de forma conservadora** — nomes/papéis só são usados quando título, descrição, legenda ou transcrição sustentam a informação; não há identificação de celebridades pelo rosto.
- **Ajuda diretamente a seleção e o Final Guard** — o mesmo mapa editorial é usado para decidir onde a ideia começa e onde realmente termina.

A regra principal da v9.8 é: **primeiro entender o vídeo; depois escolher o corte**.

## Quality v9.7 — Social Caption original

A v9.7 introduziu a publicação contextual usando título, descrição, canal/uploader, tags, hook final e transcrição. A v9.9 substitui o fallback antigo que podia transformar palavras frequentes da transcrição em hashtags sem sentido.

## Quality v9.6 — Camera Director mais calmo

- Active Speaker mais conservador e menos influenciado por movimento aleatório.
- Troca de câmera somente quando o novo falante permanece confirmado.
- Menos ida e volta em interjeições curtas.
- Micro punch-ins muito rápidos são neutralizados.
- Enquadramento mais aberto, preservando cabeça, ombros e contexto.
- Split-screen também usa crop mais aberto.

## Quality v9.5 — Hook Guard

- Reassocia o hook quando o Final Guard altera o fim do corte.
- Hook final de 4–8 palavras, preferencialmente 5–7.
- Rejeita clickbait genérico.
- Usa fallback factual do próprio clip quando a IA não responde.
- Duração configurável de 1–15 s; padrão 8 s.

## Quality v9.4 — Active Speaker Lock

- Em Podcast/Entrevista e Talking Head, prioriza atividade específica da boca durante fala.
- Whisper + energia do áudio funcionam como gate de fala.
- Mantém o falante confirmado durante momentos ambíguos.
- Rastreamento local ajuda em cenas com 3+ pessoas.
- Split-screen exige duas pessoas distintas.

## Quality v9.3 — Final Guard + Auto Review

- Revisa fala cortada, ideia incompleta e começo indevido de novo assunto.
- Pode avançar até a conclusão ou voltar ao fechamento anterior.
- Auto Review verifica MP4, legendas, centralização e split-screen.
- Problemas corrigíveis podem causar uma segunda renderização.
- Legenda tamanho **70**, branca, inferior e sem destaque automático.

## Split-screen e Visual Director

- Split-screen adaptativo, sem duração fixa por evento.
- Falante e ouvinte precisam ser identidades visuais distintas no mesmo contexto.
- Face-box X/Y e eye-line ajudam no crop.
- Visual Attention pode introduzir mudanças estáticas quando o plano fica parado demais.
- Reaction Emphasis pode destacar uma reação forte quando isso ajuda a narrativa.

## Recursos preservados

- Detecção de cenas originais.
- Perfis `Auto`, `Podcast/Entrevista`, `Talking Head`, `Gameplay` e `Filme/Série`.
- Duração inteligente entre 25 e 150 s.
- Final forte e loop natural quando apropriado.
- Hook contextual no topo.
- Legendas tamanho 70.
- Capa automática 1080×1920.
- Auto Review antes do upload.
- Source Intelligence antes da seleção.
- Social Caption Guard antes da publicação.

## Como testar

1. Abra **Actions → AutoClip - Processar vídeo → Run workflow**.
2. Cole o link do YouTube.
3. Confirme que possui direito/permissão para reutilizar o conteúdo.
4. Para avaliar a v9.9, use `1` corte e Whisper `base`.
5. Escolha o perfil adequado ou deixe `Auto`.
6. Mantenha hook, Visual Attention, Split-screen, Reaction Emphasis e Auto Review conforme desejado.
7. Mantenha a capa ativada.
8. Deixe **Enviar ao Buffer/TikTok** desmarcado no primeiro teste.
9. Execute o workflow.

No log procure por `Social Caption Guard corte 1:` e por `PREVIEW: legenda Buffer/TikTok:`. No Job Summary aparecem **Source Intelligence v9.8** e **Social Caption Guard v9.9**.

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
