# AutoClip Actions — Quality v9.8

O AutoClip processa um link do YouTube em GitHub Actions, entende o contexto do vídeo antes de escolher os cortes, cria edição vertical 1080×1920, legendas, hook, capa, direção visual, revisão automática e legenda social para Buffer/TikTok.

O computador pessoal não precisa permanecer ligado depois que o workflow começa.

## Fluxo

YouTube metadata/legendas → download → Whisper → **Source Intelligence** → seleção editorial → duração inteligente → Final Guard → Hook Guard → cenas/falantes → Active Speaker Lock → Camera Director → enquadramento → render → Auto Review → capa → Social Caption → Cloudinary → Buffer → TikTok.

## Quality v9.8 — Source Intelligence

A v9.8 preserva tudo da v9.7 e adiciona uma etapa editorial **antes da escolha dos cortes**.

- **Lê o título do YouTube** — o título deixa de ser apenas referência da fonte e passa a fazer parte do contexto editorial.
- **Lê descrição, canal/uploader, tags, categorias e capítulos** — quando esses dados existem, ajudam o AutoClip a entender formato, tema, participantes e estrutura do vídeo.
- **Tenta ler a legenda nativa do YouTube** — prioriza legenda manual e depois automática, usando formatos `json3` ou `vtt` quando disponíveis.
- **Fallback seguro para Whisper** — se a legenda nativa estiver ausente, protegida ou não puder ser baixada, o processamento continua usando a transcrição completa do Whisper.
- **Analisa a linha do tempo inteira** — a transcrição é compactada preservando começo, meio e fim, em vez de olhar apenas os primeiros minutos.
- **Mapa de assuntos** — o Source Intelligence procura blocos de assunto, mudanças de tema, histórias/perguntas completas e possíveis transições.
- **Contexto global + contexto local** — diferencia o assunto geral do vídeo da ideia específica de cada momento.
- **Participantes de forma conservadora** — nomes/papéis só são usados quando título, descrição, legenda ou transcrição sustentam a informação; não há identificação de celebridades pelo rosto.
- **Ajuda diretamente a seleção** — o seletor recebe o resumo, participantes, mapa de assuntos e orientação editorial antes de decidir `start` e `end`.
- **Ajuda diretamente o Final Guard** — a mesma inteligência é passada para a revisão do encerramento, reduzindo cortes que terminam no meio de uma ideia ou já dentro do próximo assunto.
- **Uma chamada consolidada** — quando Gemini está disponível, a análise da fonte usa uma única chamada adicional. Se a quota estiver indisponível, metadata, capítulos, descrição e Whisper ainda formam um fallback editorial.

A regra principal da v9.8 é: **primeiro entender o vídeo; depois escolher o corte**.

O Job Summary mostra o título, canal, tipo de conteúdo, leitura global, participantes sustentados pelo contexto, quantidade de blocos de assunto e se a legenda nativa do YouTube foi usada ou se o Whisper serviu de fallback.

## Quality v9.7 — Social Caption para Buffer/TikTok

- **Contexto do vídeo + contexto do corte** — a publicação usa título, descrição, canal/uploader, tags, hook final e transcrição do corte.
- **Pessoa pública de forma conservadora** — atores, YouTubers, músicos, atletas, diretores e creators podem entrar na legenda/hashtags quando metadata/transcrição sustentam essa atribuição.
- **Legenda curta estilo hook** — normalmente 5–14 palavras, factual e específica ao corte.
- **Hashtags para descoberta** — tenta produzir 12–18 hashtags únicas e relacionadas ao conteúdo.
- **Tags específicas + amplas** — pode misturar pessoa pública, filme/série/franquia/game, assunto, interview/podcast, nicho e algumas tags amplas.
- **Sem spam aleatório** — não inventa celebridades ou franquias apenas para alcançar views.
- **Preview da publicação** — com Buffer desligado, o log mostra a legenda e as hashtags que seriam enviadas.

Exemplo de formato quando o conteúdo realmente sustentar uma entrevista de Spider-Man:

`Tom Holland on the moment everything changed`

`#TomHolland #SpiderMan #Marvel #Zendaya #Interview #Movie #Actors #MCU #Film #Cinema #MovieClips #Entertainment #ViralClips #TikTok`

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
- Social Caption + hashtags para Buffer/TikTok.

## Como testar

1. Abra **Actions → AutoClip - Processar vídeo → Run workflow**.
2. Cole o link do YouTube.
3. Confirme que possui direito/permissão para reutilizar o conteúdo.
4. Para avaliar a v9.8, use `1` corte e Whisper `base`.
5. Escolha o perfil adequado ou deixe `Auto`.
6. Mantenha hook, Visual Attention, Split-screen, Reaction Emphasis e Auto Review conforme desejado.
7. Mantenha a capa ativada.
8. Deixe **Enviar ao Buffer/TikTok** desmarcado no primeiro teste.
9. Execute o workflow.

Procure no Job Summary por **Source Intelligence v9.8**. Ali aparece o que o sistema entendeu do vídeo **antes** de escolher o corte. No log de Preview também aparece a legenda social que seria enviada ao Buffer/TikTok.

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
