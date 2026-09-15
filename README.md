# AutoClip Actions — Quality v9.1

O AutoClip processa um link do YouTube em GitHub Actions, escolhe histórias curtas com contexto e payoff, cria edição vertical 1080×1920, legendas, hook, capa automática e uma camada de direção visual. Quando autorizado, envia os vídeos ao Cloudinary e à fila do Buffer/TikTok.

O computador pessoal não precisa permanecer ligado depois que o workflow começa.

## Fluxo

YouTube → Whisper → análise do vídeo inteiro → perfil de conteúdo → duração inteligente → contexto/final → cenas/falantes → Visual Director → enquadramento refinado → legendas/hook → capa → Cloudinary → Buffer → TikTok.

## Quality v9.1 — Visual Director refinado

A v9.1 preserva os recursos da v9 e acrescenta três ajustes de qualidade:

- **Enquadramento facial refinado** — cada plano amostra vários frames e acompanha o rosto da pessoa escolhida. O centro final usa uma trilha estável do mesmo rosto em vez de depender de uma única detecção. Quando a pessoa está perto da borda da fonte, o crop pode ficar um pouco mais fechado para conseguir centralizá-la melhor no 9:16.
- **Split-screen mais presente** — em `Podcast/Entrevista`, os split-screens úteis podem durar aproximadamente 2.7–4.2 s. O limite também cresce moderadamente conforme a duração do clip, chegando a mais ocorrências apenas em vídeos longos. Ainda há espaçamento e score mínimo de reação para evitar exagero.
- **Legendas sem destaque automático** — as legendas continuam em tamanho 65, posição inferior e blocos curtos, mas nenhuma palavra muda automaticamente de cor, peso ou tamanho.

## Visual Director

- **Visual Attention Engine** — mede atividade visual dentro dos planos. Quando um trecho permanece com pouca mudança por tempo suficiente, pode introduzir um único `punch-in` estático, alinhado próximo a uma divisão de fala. Não usa pan/zoom contínuo.
- **Split-screen inteligente** — em `Podcast/Entrevista`, procura duas pessoas persistentes e separadas no quadro. Quando a segunda pessoa apresenta uma reação útil, mostra temporariamente o falante na metade superior e a reação na inferior.
- **Reaction emphasis** — quando a segunda pessoa apresenta atividade facial mais forte, ou quando há sinais textuais de reação como risada/surpresa, o sistema pode fazer um close estático curto nela e depois voltar ao falante.

Existe um **orçamento visual**: as intervenções precisam respeitar espaçamento e limites por clip. Se o vídeo já estiver visualmente variado, o sistema pode simplesmente manter a edição existente.

## Recursos preservados

- **Falante consciente** — combina timing do Whisper, energia do áudio, rostos e atividade facial para manter foco no provável falante.
- **Detecção de cenas** — respeita mudanças de câmera/cena do vídeo original.
- **Perfis** — `Auto`, `Podcast/Entrevista`, `Talking Head`, `Gameplay` e `Filme/Série`.
- **Duração inteligente** — trabalha entre 25 e 150 s e tenta usar o menor trecho que entregue contexto + desenvolvimento + payoff.
- **Final forte** — prioriza conclusão, resposta, punchline, surpresa, decisão ou afirmação forte; pode estender o fim alguns segundos para fechar a ideia.
- **Loop natural** — quando existe, prefere um fim que reconecte bem ao começo sem duplicar falas.
- **Hook contextual** — factual, curto, opcional e com duração configurável.
- **Legendas** — tamanho 65, posição inferior, blocos curtos e estilo uniforme, sem destaques automáticos.
- **Capa automática** — escolhe um frame forte do clip, cria thumbnail 1080×1920 com estilos `Auto`, `Clean`, `Bold` ou `Cinematic`, insere a capa no início do MP4 e também a salva separadamente no Cloudinary.

## Controles visuais

No formulário **Run workflow** permanecem os controles:

- **Visual Attention Engine** — ativa/desativa mudanças por estagnação visual.
- **Split-screen inteligente** — ativa/desativa o layout temporário falante + reação.
- **Reaction emphasis** — ativa/desativa closes curtos de reação.

A antiga opção de destaque inteligente das legendas foi removida. Para Gameplay o sistema evita punch-ins artificiais do Visual Attention para não esconder HUD/ação. Split-screen é direcionado a Podcast/Entrevista.

## Como testar

1. Abra **Actions → AutoClip - Processar vídeo → Run workflow**.
2. Cole o link do YouTube.
3. Confirme que possui direito/permissão para reutilizar o conteúdo.
4. Para avaliar v9.1, escolha `1` corte, Whisper `base`, legenda `English` e perfil `Podcast/Entrevista`.
5. Deixe **Visual Attention Engine**, **Split-screen inteligente** e **Reaction emphasis** marcados.
6. Mantenha hook e capa ativados.
7. Deixe **Enviar ao Buffer/TikTok** desmarcado durante os testes.
8. Execute o workflow.

O Job Summary mostra o vídeo, a capa, duração/final escolhidos e a descrição da edição visual aplicada.

## Secrets obrigatórios

Em **Settings → Secrets and variables → Actions → Repository secrets**:

- `CLOUDINARY_CLOUD_NAME`
- `CLOUDINARY_API_KEY`
- `CLOUDINARY_API_SECRET`
- `BUFFER_API_KEY`
- `YOUTUBE_COOKIES_B64`

Opcionais:

- `BUFFER_CHANNEL_ID`
- `GEMINI_API_KEY`

Repository variables opcionais:

- `GEMINI_MODEL`
- `CLOUDINARY_FOLDER` = `autoclips`

## Segurança

Nunca coloque API keys ou cookies diretamente no código. Use GitHub Actions Secrets.

## Direitos autorais

Use o fluxo somente com material próprio, licenciado ou que você tenha autorização para reutilizar. Cortar, legendar, reenquadrar, criar capa ou adicionar efeitos não remove os direitos autorais do conteúdo original.
