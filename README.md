# AutoClip Actions — Quality v6

Processa um link do YouTube em um runner do GitHub Actions, cria cortes verticais 1080×1920 com legendas, envia os MP4 ao Cloudinary e, quando autorizado no formulário, adiciona os posts à fila do Buffer/TikTok.

O computador pessoal não precisa ficar ligado depois que o workflow foi iniciado.

## Fluxo

YouTube → Whisper → análise do vídeo inteiro → perfil de conteúdo → validação de contexto → cenas/falantes → edição 9:16 → Cloudinary → Buffer → TikTok.

## Quality v6

A v6 adiciona quatro camadas principais de edição:

- **Falante consciente:** em Podcast/Entrevista e Talking Head, combina tempo das falas do Whisper, atividade do áudio, detecção de rostos e movimento da região da boca. Mantém identidades visuais do tipo Speaker A/B/C durante o corte e usa cortes secos, não movimentos artificiais de câmera.
- **Detecção de cenas:** procura mudanças reais de câmera/cena no vídeo original e tenta alinhar os reenquadramentos a essas mudanças. Cortes artificiais extras só são usados quando o plano original fica longo demais.
- **Perfis de conteúdo:** `Auto`, `Podcast/Entrevista`, `Talking Head`, `Gameplay` e `Filme/Série`.
- **Contexto/hook:** a seleção avalia explicitamente se uma pessoa que nunca viu o vídeo entende os primeiros 5 segundos. Se faltar contexto, tenta começar antes; candidatos que não podem ser corrigidos são descartados. Um hook factual curto no topo pode ser ativado ou desativado.

As legendas usam blocos curtos, posição inferior e tamanho 60 em uma composição 1080×1920.

## Perfis

- **Auto** — tenta classificar o vídeo usando título e presença/quantidade de rostos.
- **Podcast/Entrevista** — prioriza falantes, interlocutores e reaction shots curtos; usa áudio + rosto + timing.
- **Talking Head** — mantém foco na pessoa principal, com planos mais longos e estáveis.
- **Gameplay** — evita depender de rostos e usa atividade visual para escolher a região do enquadramento.
- **Filme/Série** — prioriza os cortes de cena originais e centraliza rosto/ação sem criar movimentos contínuos de câmera.

## Secrets obrigatórios

Em **Settings → Secrets and variables → Actions → Repository secrets**, crie:

- `CLOUDINARY_CLOUD_NAME`
- `CLOUDINARY_API_KEY`
- `CLOUDINARY_API_SECRET`
- `BUFFER_API_KEY`
- `YOUTUBE_COOKIES_B64`

Opcionais:

- `BUFFER_CHANNEL_ID` — se vazio, o script tenta localizar o primeiro canal TikTok conectado ao Buffer.
- `GEMINI_API_KEY` — usado para seleção editorial, contexto, hook, metadata e traduções quando necessárias. Se indisponível, existem fallbacks locais, mas alguns recursos editoriais ficam menos inteligentes.

Em **Repository variables**, opcionalmente crie:

- `GEMINI_MODEL`
- `CLOUDINARY_FOLDER` = `autoclips`

## Buffer

Conecte sua conta TikTok ao Buffer e configure a agenda do canal com os horários desejados, por exemplo:

- 12:00
- 18:00
- 22:00

No formulário do workflow, **Enviar os cortes ao Buffer/TikTok?** fica desmarcado por padrão para permitir testes em modo preview.

## Como processar um vídeo

1. Abra **Actions → AutoClip - Processar vídeo → Run workflow**.
2. Cole o link do YouTube.
3. Confirme que você possui direito/permissão para reutilizar o conteúdo.
4. Escolha 1, 2 ou 3 cortes.
5. Escolha `base` (recomendado) ou `tiny` (teste rápido) no Whisper.
6. Escolha o idioma da legenda: `English`, `Português (Brasil)` ou `Idioma original`.
7. Escolha o perfil de conteúdo ou deixe em `Auto`.
8. Ative/desative o hook contextual no topo.
9. Deixe a publicação no Buffer desmarcada enquanto estiver avaliando a qualidade.
10. Clique em **Run workflow**.

O progresso aparece no log do job. O **Job summary** mostra os links do Cloudinary e informações de edição do corte.

## Segurança

Nunca coloque API keys ou cookies diretamente no código. Use somente GitHub Actions Secrets.

## Direitos autorais

Use o fluxo apenas com material próprio, licenciado ou que você tenha autorização para reutilizar. Cortar, legendar ou reenquadrar um vídeo não remove os direitos autorais do conteúdo original.
