# AutoClip Actions — Quality v8

Processa um link do YouTube em um runner do GitHub Actions, cria cortes verticais 1080×1920 com legendas, hook, enquadramento inteligente e capa automática, envia os MP4 ao Cloudinary e, quando autorizado, adiciona os posts à fila do Buffer/TikTok.

O computador pessoal não precisa ficar ligado depois que o workflow foi iniciado.

## Fluxo

YouTube → Whisper → análise do vídeo inteiro → perfil de conteúdo → seleção por retenção/contexto → final/payoff → cenas/falantes → edição 9:16 → legendas inteligentes → capa automática → Cloudinary → Buffer → TikTok.

## Quality v8

A v8 preserva as funções anteriores e acrescenta três camadas voltadas à retenção de quem está assistindo:

- **Duração inteligente:** os cortes agora podem variar aproximadamente de 25 a 150 segundos. O editor procura o menor trecho que ainda entregue contexto/setup, desenvolvimento e payoff. Um assunto completo em 35 ou 47 segundos não é esticado artificialmente para 60 segundos; uma história que realmente precise de 80 ou 120 segundos pode permanecer mais longa.
- **Final forte:** a seleção avalia especificamente os últimos 5–10 segundos e prioriza conclusão, resposta, punchline, surpresa, decisão ou afirmação forte. Se o corte terminar de maneira fraca ou incompleta, pode avançar alguns segundos para fechar a ideia; candidatos claramente truncados podem ser rejeitados.
- **Loop editorial natural:** quando ativado, a IA pode preferir um fechamento cuja última ideia reconecte naturalmente com a abertura no replay. O AutoClip não duplica falas nem cria um loop artificial; ele apenas escolhe um fim que funcione melhor quando a plataforma reinicia o vídeo.
- **Ênfase inteligente nas legendas:** mantém a fonte base em tamanho 65 e a posição inferior já aprovada. Valores, números e algumas palavras de alto impacto podem receber uma única ênfase discreta em amarelo, negrito e tamanho 72 dentro daquele bloco. Não existe animação palavra por palavra.

## Recursos preservados

- **Falante consciente:** em Podcast/Entrevista e Talking Head, combina tempo das falas do Whisper, atividade do áudio, detecção de rostos e movimento da região da boca. Mantém identidades visuais do tipo Speaker A/B/C durante o corte e usa cortes secos, não movimentos artificiais de câmera.
- **Detecção de cenas:** procura mudanças reais de câmera/cena no vídeo original e tenta alinhar os reenquadramentos a essas mudanças.
- **Perfis de conteúdo:** `Auto`, `Podcast/Entrevista`, `Talking Head`, `Gameplay` e `Filme/Série`.
- **Contexto/hook:** avalia se alguém que nunca viu o original entende os primeiros 5 segundos. Se faltar contexto, tenta começar antes. O hook factual no topo pode ser ativado/desativado e sua duração é configurável.
- **Capa automática:** analisa vários frames do corte e cria uma capa 1080×1920 com título derivado do hook ou da própria fala, nos estilos `Auto`, `Clean`, `Bold` ou `Cinematic`.
- **Cover no MP4:** a capa pode ser colocada no início do vídeo por um período configurável (padrão `0.8 s`) e também é enviada separadamente ao Cloudinary para preview.

## Perfis

- **Auto** — tenta classificar o vídeo usando título e presença/quantidade de rostos.
- **Podcast/Entrevista** — prioriza falantes, interlocutores e reaction shots curtos; usa áudio + rosto + timing.
- **Talking Head** — mantém foco na pessoa principal, com planos mais longos e estáveis.
- **Gameplay** — usa atividade visual para escolher a região relevante sem depender de rostos.
- **Filme/Série** — respeita mudanças de cena e centraliza rosto/ação sem movimentos contínuos artificiais.

## Controles no workflow

Além do link, quantidade de cortes, Whisper, idioma e perfil, o formulário permite:

- ligar/desligar o hook e configurar sua duração;
- ligar/desligar a **ênfase inteligente das legendas**;
- ligar/desligar a preferência por **loop editorial natural**;
- ligar/desligar a capa automática, escolher `Auto`, `Clean`, `Bold` ou `Cinematic` e definir quanto tempo ela aparece no início;
- manter o envio ao Buffer/TikTok desligado durante testes.

A duração inteligente é automática: o intervalo técnico atual é de **25 a 150 segundos**, mas o objetivo do editor é usar somente o tempo necessário para a história ficar completa.

## Secrets obrigatórios

Em **Settings → Secrets and variables → Actions → Repository secrets**, crie:

- `CLOUDINARY_CLOUD_NAME`
- `CLOUDINARY_API_KEY`
- `CLOUDINARY_API_SECRET`
- `BUFFER_API_KEY`
- `YOUTUBE_COOKIES_B64`

Opcionais:

- `BUFFER_CHANNEL_ID` — se vazio, o script tenta localizar o primeiro canal TikTok conectado ao Buffer.
- `GEMINI_API_KEY` — usado para seleção editorial, contexto, hook, metadata e traduções quando necessárias. Sem ele, existem fallbacks locais, mas algumas decisões editoriais ficam menos inteligentes.

Em **Repository variables**, opcionalmente crie:

- `GEMINI_MODEL`
- `CLOUDINARY_FOLDER` = `autoclips`

## Buffer

Conecte sua conta TikTok ao Buffer e configure a agenda do canal com os horários desejados, por exemplo 12:00, 18:00 e 22:00. O envio ao Buffer/TikTok fica desmarcado por padrão para permitir testes em preview.

## Como processar um vídeo

1. Abra **Actions → AutoClip - Processar vídeo → Run workflow**.
2. Cole o link do YouTube e confirme os direitos/permissão.
3. Escolha 1, 2 ou 3 cortes e o modelo Whisper (`base` recomendado).
4. Escolha idioma e perfil do conteúdo.
5. Configure hook, ênfase da legenda e loop natural.
6. Configure a capa automática.
7. Deixe Buffer/TikTok desmarcado enquanto estiver avaliando qualidade.
8. Clique em **Run workflow**.

O **Job summary** mostra links dos vídeos/capas e, na v8, também informa duração escolhida, tipo/força do final e se o corte foi considerado naturalmente loopável.

## Segurança

Nunca coloque API keys ou cookies diretamente no código. Use somente GitHub Actions Secrets.

## Direitos autorais

Use o fluxo apenas com material próprio, licenciado ou que você tenha autorização para reutilizar. Cortar, legendar, reenquadrar ou criar uma capa não remove os direitos autorais do conteúdo original.
