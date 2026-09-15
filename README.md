# AutoClip Actions — Quality v9.3

O AutoClip processa um link do YouTube em GitHub Actions, escolhe histórias curtas com contexto e payoff, cria edição vertical 1080×1920, legendas, hook, capa automática, direção visual e uma revisão automática antes do upload. Quando autorizado, envia os vídeos ao Cloudinary e à fila do Buffer/TikTok.

O computador pessoal não precisa permanecer ligado depois que o workflow começa.

## Fluxo

YouTube → Whisper → análise do vídeo inteiro → perfil de conteúdo → duração inteligente → revisão do encerramento → cenas/falantes → Visual Director → enquadramento por rosto → render → Auto Review → correção automática quando necessária → capa → Cloudinary → Buffer → TikTok.

## Quality v9.3 — Final Guard + Auto Review

A v9.3 preserva a v9.2 e acrescenta uma camada de controle de qualidade.

- **Proteção do final** — depois da escolha do trecho, uma revisão editorial específica analisa o final de todos os cortes em uma única etapa. O sistema verifica se alguém foi cortado no meio da fala, se a ideia ficou incompleta, se existe um fechamento melhor ou se o corte avançou para o começo de um assunto novo.
- **Correção do encerramento** — quando necessário, o final pode avançar até a conclusão da mesma ideia ou voltar para o fechamento imediatamente anterior ao novo assunto. O tempo é alinhado aos finais de segmentos do Whisper.
- **Auto Review do MP4** — depois do primeiro render, o AutoClip revisa resolução, timing/estrutura das legendas, centralização dos rostos e consistência do split-screen.
- **Auto-correção visual** — se o rosto estiver persistentemente fora do enquadramento, o clip é renderizado novamente uma vez usando um crop mais rigoroso e centralizado.
- **Auto-correção do split** — se um split final não apresentar duas telas válidas ou parecer duplicado, aquele split é removido na segunda renderização e o plano volta para a pessoa principal.
- **Falha crítica bloqueia upload** — resolução incorreta ou legenda estruturalmente inválida impede o upload, em vez de publicar um arquivo quebrado.
- **Legenda tamanho 70** — as legendas permanecem uniformes, brancas, inferiores e sem destaque automático de palavras, agora em tamanho 70.

## Split-screen e enquadramento da v9.2 preservados

- **Split-screen adaptativo** — não possui duração fixa por evento. Começa quando duas pessoas distintas permanecem relevantes e termina quando essa condição deixa de existir.
- **Duas identidades distintas** — falante e ouvinte são validados no mesmo frame; faces sobrepostas/próximas demais são rejeitadas.
- **Face-box + eye-line** — posição X/Y e tamanho do rosto são usados no crop. O rosto tende a ficar no terço superior quando a fonte permite.
- **Painéis independentes** — cada metade do split tem crop próprio para sua pessoa.

O conjunto dos splits continua com orçamento total para não dominar o vídeo inteiro.

## Visual Director

- **Visual Attention Engine** — mede atividade visual e pode criar um punch-in estático quando o plano fica parado demais.
- **Split-screen inteligente** — em Podcast/Entrevista, mostra falante + segunda pessoa somente quando as duas identidades são válidas e persistentes.
- **Reaction emphasis** — reações fortes podem receber um close curto antes de voltar ao falante.

## Recursos preservados

- **Falante consciente** — combina timing do Whisper, energia do áudio, rostos e atividade facial.
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

A antiga opção de destaque inteligente das legendas continua removida.

## Como testar

1. Abra **Actions → AutoClip - Processar vídeo → Run workflow**.
2. Cole o link do YouTube.
3. Confirme que possui direito/permissão para reutilizar o conteúdo.
4. Para avaliar a v9.3, use `1` corte, Whisper `base`, legenda `English` e perfil `Podcast/Entrevista`.
5. Deixe Visual Attention, Split-screen, Reaction emphasis e **Auto Review** ativados.
6. Mantenha hook e capa ativados.
7. Deixe **Enviar ao Buffer/TikTok** desmarcado durante o teste.
8. Execute o workflow.

O Job Summary mostra a revisão do encerramento e informa se o Auto Review aprovou o clip diretamente ou precisou aplicar uma correção automática.

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
