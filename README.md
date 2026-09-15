# AutoClip Actions — Quality v9.7

O AutoClip processa um link do YouTube em GitHub Actions, escolhe histórias curtas com contexto e payoff, cria edição vertical 1080×1920, legendas, hook, capa automática, direção visual, revisão automática e agora também gera a legenda social que será enviada ao Buffer/TikTok.

O computador pessoal não precisa permanecer ligado depois que o workflow começa.

## Fluxo

YouTube → Whisper → análise do vídeo inteiro → perfil de conteúdo → duração inteligente → revisão do encerramento → Hook Guard → cenas/falantes → Active Speaker Lock → Camera Director → enquadramento → render → Auto Review → correção automática → capa → Social Caption → Cloudinary → Buffer → TikTok.

## Quality v9.7 — Social Caption para Buffer/TikTok

A v9.7 preserva a v9.6 e adiciona uma camada específica de publicação social.

- **Contexto do vídeo + contexto do corte** — a legenda usa título, descrição, canal/uploader, tags do YouTube, hook final e transcrição do próprio corte.
- **Pessoa pública de forma conservadora** — nomes de atores, YouTubers, músicos, atletas, diretores, creators etc. podem entrar na legenda/hashtags quando o metadata/transcrição sustenta essa atribuição. O sistema não identifica celebridades pelo rosto e prefere omitir um nome quando existe dúvida sobre quem está falando.
- **Legenda curta estilo hook** — normalmente 5–14 palavras, uma frase curta, factual e relacionada especificamente ao corte.
- **Hashtags para descoberta** — tenta produzir 12–18 hashtags únicas quando existem conceitos relevantes suficientes.
- **Hashtags específicas + amplas** — mistura nomes públicos, filme/série/franquia/game/topic, formato como interview/podcast, nicho/categoria e algumas tags amplas de descoberta.
- **Sem spam aleatório** — não adiciona celebridades, franquias ou assuntos que não estejam sustentados pelo vídeo/corte apenas para tentar alcançar views.
- **Uma chamada em lote** — os metadados sociais dos cortes são preparados juntos para economizar quota do Gemini.
- **Fallback factual** — se o Gemini estiver indisponível, a legenda usa o hook/trecho e cria hashtags a partir do título, canal e palavras-chave do clip.
- **Preview mostra a publicação** — com Buffer desativado, o log mostra exatamente a legenda + hashtags que seriam enviadas.

Exemplo esperado quando o contexto realmente for uma entrevista com elenco de Spider-Man:

`Tom Holland on the moment everything changed`

`#TomHolland #SpiderMan #Marvel #Zendaya #Interview #Movie #Actors #MCU #Film #Cinema #MovieClips #Entertainment #ViralClips #TikTok`

Os nomes/tags acima são apenas um exemplo de formato; a v9.7 só os utiliza quando o conteúdo processado dá suporte.

## Quality v9.6 — Camera Director mais calmo

- **Falante ativo mais conservador** — analisa movimento da boca em vários frames e reduz influência de movimento geral da cabeça.
- **Troca de câmera sustentada** — um novo falante precisa permanecer como candidato forte antes da troca; pequenas incertezas mantêm o último falante confirmado.
- **Menos troca em interjeições curtas** — evita ir e voltar por respostas rápidas ou ruído visual.
- **Micro punch-ins reduzidos** — intervenções visuais curtas demais são neutralizadas.
- **Enquadramento mais aberto** — preserva mais cabeça, ombros e contexto, inclusive nas correções do Auto Review.
- **Split-screen mais aberto** — painéis continuam centralizados, com menos zoom no rosto.

## Quality v9.5 — Hook Guard

- **Hook não se perde quando o final muda** — o Final Guard pode alterar o `end` do corte; a v9.5 reassocia o hook ao intervalo final.
- **Revisão final do texto** — quando possível, os hooks passam por revisão semântica depois que os cortes já estão definidos.
- **Texto natural** — 4–8 palavras, preferencialmente 5–7.
- **Sem clickbait genérico** — rejeita frases como `You won't believe`, `Watch until the end` e `This is crazy`.
- **Fallback factual** — se a IA estiver indisponível, extrai uma frase curta do próprio clip.
- **Duração configurável** — padrão 8 segundos, faixa 1–15.

## Quality v9.4 — Active Speaker Lock

- **Prioridade para quem está falando** — em Podcast/Entrevista e Talking Head, compara atividade específica da região da boca.
- **Áudio + Whisper como gate** — movimento facial ganha peso forte quando existe fala naquele instante.
- **Active speaker locking** — mantém a pessoa confirmada durante pequenos momentos ambíguos.
- **Melhor comportamento com 3+ pessoas** — rostos são acompanhados como trilhas locais.
- **Split-screen protegido** — a pessoa superior tende a ser o falante e a segunda tela precisa ser outra pessoa distinta.

## Quality v9.3 — Final Guard + Auto Review

- **Proteção do final** — revisa fala cortada, ideia incompleta e começo indevido de assunto novo.
- **Correção do encerramento** — pode avançar até a conclusão ou voltar ao fechamento anterior.
- **Auto Review do MP4** — revisa resolução, legendas, centralização dos rostos e split-screen.
- **Auto-correção visual** — problemas corrigíveis podem causar uma segunda renderização.
- **Falha crítica bloqueia upload** — arquivo estruturalmente inválido não é publicado.
- **Legenda tamanho 70** — branca, inferior, uniforme e sem destaque automático.

## Split-screen e Visual Director

- **Split-screen adaptativo** — não possui duração fixa; começa e termina conforme presença/relevância de duas pessoas distintas.
- **Duas identidades distintas** — falante e ouvinte são validados no mesmo frame.
- **Face-box + eye-line** — posição X/Y e tamanho do rosto entram no crop.
- **Visual Attention Engine** — pode introduzir mudanças estáticas quando um plano fica visualmente parado, sem voltar à câmera flutuante.
- **Reaction emphasis** — reações fortes ainda podem receber um close curto quando isso ajuda.

## Recursos preservados

- Detecção de cenas originais.
- Perfis `Auto`, `Podcast/Entrevista`, `Talking Head`, `Gameplay` e `Filme/Série`.
- Duração inteligente entre 25 e 150 s.
- Final forte e loop natural quando apropriado.
- Hook contextual no topo.
- Legendas tamanho 70.
- Capa automática 1080×1920.
- Auto Review antes do upload.

## Como testar

1. Abra **Actions → AutoClip - Processar vídeo → Run workflow**.
2. Cole o link do YouTube.
3. Confirme que possui direito/permissão para reutilizar o conteúdo.
4. Use `1` corte, Whisper `base` e o perfil adequado ao vídeo.
5. Deixe hook, Visual Attention, Split-screen, Reaction emphasis e Auto Review conforme desejado.
6. Mantenha a capa ativada.
7. Deixe **Enviar ao Buffer/TikTok** desmarcado durante o primeiro teste da v9.7.
8. Execute o workflow.

No modo Preview, procure no log por `PREVIEW: legenda Buffer/TikTok:`. O Job Summary também mostra a legenda curta, pessoas públicas atribuídas com segurança, papéis quando disponíveis e a lista de hashtags de cada corte.

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
