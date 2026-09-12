# AutoClip Actions v2.0

Processa um link do YouTube em um runner do GitHub Actions, cria cortes verticais com legendas, envia os MP4 ao Cloudinary e adiciona os posts à fila do Buffer/TikTok.

## Fluxo

YouTube → Whisper → AutoClip Score → Gemini opcional → FFmpeg 9:16 → Cloudinary → Buffer → TikTok.

O computador pessoal não precisa ficar ligado depois que o workflow foi iniciado.

## Secrets obrigatórios

Em **Settings → Secrets and variables → Actions → Repository secrets**, crie:

- `CLOUDINARY_CLOUD_NAME`
- `CLOUDINARY_API_KEY`
- `CLOUDINARY_API_SECRET`
- `BUFFER_API_KEY`

Opcionais:

- `BUFFER_CHANNEL_ID` — se vazio, o script tenta localizar o primeiro canal TikTok conectado ao Buffer.
- `GEMINI_API_KEY` — se vazio, títulos/legendas usam fallback local e conteúdo estrangeiro não é traduzido automaticamente.

Em **Repository variables**, opcionalmente crie:

- `GEMINI_MODEL` = `gemini-3.8-flash`
- `CLOUDINARY_FOLDER` = `autoclips`

## Buffer

Conecte sua conta TikTok ao Buffer e configure a agenda do canal com três horários diários:

- 12:00
- 18:00
- 22:00

O workflow adiciona os cortes à fila do Buffer; o Buffer usa os próximos horários livres dessa agenda.

## Como processar um vídeo

1. Abra a aba **Actions** do repositório.
2. Entre em **AutoClip - Processar vídeo**.
3. Clique em **Run workflow**.
4. Cole o link do YouTube.
5. Confirme que você possui direito/permissão para reutilizar o conteúdo.
6. Escolha `tiny` (rápido) ou `base` (mais preciso).
7. Clique em **Run workflow**.

O progresso aparece no log do job e o **Job summary** mostra os links do Cloudinary e os IDs dos posts no Buffer.

## Segurança

Nunca coloque API keys diretamente no código. Use somente GitHub Actions Secrets.

## Direitos autorais

Use o fluxo apenas com material próprio, licenciado ou que você tenha autorização para reutilizar. Cortar, legendar ou reenquadrar um vídeo não remove os direitos autorais do conteúdo original.
