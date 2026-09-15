# AutoClip Actions — Quality v9.5

O AutoClip processa um link do YouTube em GitHub Actions, escolhe histórias curtas com contexto e payoff, cria edição vertical 1080×1920, legendas, hook, capa automática, direção visual e uma revisão automática antes do upload. Quando autorizado, envia os vídeos ao Cloudinary e à fila do Buffer/TikTok.

O computador pessoal não precisa permanecer ligado depois que o workflow começa.

## Fluxo

YouTube → Whisper → análise do vídeo inteiro → perfil de conteúdo → duração inteligente → revisão do encerramento → Hook Guard → cenas/falantes → Active Speaker Lock → Visual Director → enquadramento por rosto → render → Auto Review → correção automática quando necessária → capa → Cloudinary → Buffer → TikTok.

## Quality v9.5 — Hook Guard

A v9.5 preserva a v9.4 e corrige o hook contextual do topo.

- **Hook não se perde quando o final muda** — o Final Guard pode alterar o `end` do corte depois que o hook foi criado. A v9.5 reassocia o hook ao intervalo FINAL, evitando que ele desapareça por diferença de timestamps.
- **Revisão final do texto do hook** — quando o Gemini está disponível, todos os hooks finais são revisados em uma única chamada, depois que os cortes já estão definidos.
- **Texto mais natural** — o hook deve ter 4–8 palavras, preferencialmente 5–7, ser compreensível para quem nunca viu o vídeo e evitar linguagem robótica.
- **Sem clickbait genérico** — frases como `You won't believe`, `Watch until the end`, `This is crazy` e equivalentes são rejeitadas.
- **Sem contexto inventado** — números, nomes e fatos só podem aparecer se estiverem presentes no próprio corte.
- **Fallback factual** — se o Gemini estiver indisponível, o AutoClip extrai uma frase curta do próprio clip em vez de simplesmente deixar o hook vazio.
- **Duração preservada** — o hook continua usando o campo `Duração do hook no topo`, com padrão de 8 segundos e faixa de 1–15 segundos.
- **Idioma preservado** — o hook segue a escolha de idioma configurada para o processamento quando a revisão semântica está disponível.

## Quality v9.4 — Active Speaker Lock

- **Prioridade real para quem está falando** — durante Podcast/Entrevista e Talking Head, o sistema amostra o plano várias vezes por segundo e compara a atividade específica da região da boca de cada rosto.
- **Áudio + Whisper como gate** — movimento facial só conta fortemente quando há fala detectada naquele instante. A energia do áudio ajuda a evitar que uma pessoa gesticulando ou mexendo a cabeça seja confundida com o falante.
- **Tamanho do rosto não domina mais** — uma pessoa grande ou central no quadro recebe apenas um bônus mínimo. O movimento labial durante fala passa a ser o principal sinal.
- **Active speaker locking** — depois que um falante é identificado, o enquadramento permanece nele durante pequenos momentos ambíguos. A câmera só troca para outra pessoa quando a evidência persiste por vários frames.
- **Troca sustentada de falante** — se outra pessoa começa realmente a falar, o mesmo plano pode ser subdividido e o crop passa para ela.
- **Melhor comportamento com 3+ pessoas** — os rostos são acompanhados como trilhas locais dentro do plano.
- **Split-screen protegido** — a pessoa superior é corrigida para o falante detectado; a segunda tela precisa ser outra pessoa distinta e persistente.

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
- **Hook contextual** — factual, curto, opcional, resiliente ao Final Guard e com duração configurável.
- **Capa automática** — cria thumbnail 1080×1920 a partir de um frame forte do próprio clip.

## Controles no Run workflow

Permanecem:

- **Mostrar hook contextual no topo?** — ativado por padrão.
- **Duração do hook no topo** — padrão 8 segundos, faixa 1–15.
- **Visual Attention Engine**
- **Split-screen inteligente**
- **Reaction emphasis**
- **Auto Review** — ativado por padrão; revisa e tenta corrigir o clip antes do upload.

O Active Speaker Lock e o Hook Guard são automáticos dentro dos perfis suportados.

## Como testar

1. Abra **Actions → AutoClip - Processar vídeo → Run workflow**.
2. Cole o link do YouTube.
3. Confirme que possui direito/permissão para reutilizar o conteúdo.
4. Use `1` corte, Whisper `base`, legenda `English` e perfil `Podcast/Entrevista`.
5. Deixe **Mostrar hook contextual no topo** ativado e `Duração do hook` em `8`.
6. Deixe Visual Attention, Split-screen, Reaction emphasis e Auto Review ativados.
7. Mantenha a capa ativada.
8. Deixe **Enviar ao Buffer/TikTok** desmarcado durante o teste.
9. Execute o workflow.

O Job Summary da v9.5 mostra o texto final do hook, sua origem (`final_ai_review`, recuperação do hook anterior ou fallback) e a duração configurada de exibição.

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
