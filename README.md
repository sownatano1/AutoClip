# AutoClip Actions — Quality v9.2

O AutoClip processa um link do YouTube em GitHub Actions, escolhe histórias curtas com contexto e payoff, cria edição vertical 1080×1920, legendas, hook, capa automática e uma camada de direção visual. Quando autorizado, envia os vídeos ao Cloudinary e à fila do Buffer/TikTok.

O computador pessoal não precisa permanecer ligado depois que o workflow começa.

## Fluxo

YouTube → Whisper → análise do vídeo inteiro → perfil de conteúdo → duração inteligente → contexto/final → cenas/falantes → Visual Director → enquadramento por rosto → legendas/hook → capa → Cloudinary → Buffer → TikTok.

## Quality v9.2

A v9.2 preserva os recursos anteriores e corrige principalmente split-screen e enquadramento.

- **Split-screen adaptativo** — não possui uma duração fixa por evento. O sistema amostra o plano continuamente e o split começa quando duas pessoas distintas permanecem relevantes no mesmo enquadramento; termina quando essa condição deixa de existir. Existe apenas um orçamento total de uso no clip para evitar exagero.
- **Duas identidades realmente distintas** — falante e ouvinte são validados no mesmo frame. Detecções sobrepostas ou próximas demais são rejeitadas, evitando mostrar a mesma pessoa nas duas metades do split.
- **Enquadramento por caixa facial** — o AutoClip deixa de usar somente um ponto horizontal. Ele acompanha posição X/Y, largura e altura do rosto em vários frames e calcula o crop usando a pessoa focal.
- **Eye-line** — quando há espaço na fonte, o rosto é colocado aproximadamente no terço superior do quadro vertical, em vez de simplesmente centralizar o frame original. O tamanho do rosto também influencia o nível de crop.
- **Split-screen também usa face-box** — cada painel recebe o enquadramento da sua própria pessoa, com posição vertical e zoom calculados separadamente.
- **Legendas uniformes** — tamanho 65, posição inferior e sem destaque automático de palavras.

## Visual Director

- **Visual Attention Engine** — mede atividade visual e pode criar um punch-in estático quando o plano fica parado demais.
- **Split-screen inteligente** — em Podcast/Entrevista, mostra falante + segunda pessoa apenas quando as duas identidades são válidas e persistentes.
- **Reaction emphasis** — reações fortes ainda podem receber um close curto antes de voltar ao falante.

O split-screen não tem um cronômetro editorial fixo na v9.2. Ele acompanha a duração real da presença/relevância das duas pessoas. Para não dominar o vídeo inteiro, o conjunto dos splits possui um orçamento de aproximadamente 46% da duração do clip e um limite de eventos.

## Recursos preservados

- **Falante consciente** — combina timing do Whisper, energia do áudio, rostos e atividade facial.
- **Detecção de cenas** — respeita mudanças de câmera/cena do vídeo original.
- **Perfis** — `Auto`, `Podcast/Entrevista`, `Talking Head`, `Gameplay` e `Filme/Série`.
- **Duração inteligente** — trabalha entre 25 e 150 s e tenta usar o menor trecho que entregue contexto + desenvolvimento + payoff.
- **Final forte** — prioriza conclusão, resposta, punchline, surpresa, decisão ou afirmação forte.
- **Loop natural** — quando existe, prefere um fim que reconecte bem ao começo sem duplicar falas.
- **Hook contextual** — factual, curto, opcional e com duração configurável.
- **Capa automática** — cria thumbnail 1080×1920 a partir de um frame forte do próprio clip.

## Controles visuais

No formulário **Run workflow** permanecem:

- **Visual Attention Engine**
- **Split-screen inteligente**
- **Reaction emphasis**

A antiga opção de destaque inteligente das legendas foi removida.

## Como testar

1. Abra **Actions → AutoClip - Processar vídeo → Run workflow**.
2. Cole o link do YouTube.
3. Confirme que possui direito/permissão para reutilizar o conteúdo.
4. Para avaliar a v9.2, use `1` corte, Whisper `base`, legenda `English` e perfil `Podcast/Entrevista`.
5. Deixe Visual Attention, Split-screen e Reaction emphasis ativados.
6. Mantenha hook e capa ativados.
7. Deixe **Enviar ao Buffer/TikTok** desmarcado durante o teste.
8. Execute o workflow.

O Job Summary mostra o vídeo, a capa, duração/final e a descrição da edição visual aplicada.

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
