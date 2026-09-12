from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import threading
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import requests

ROOT = Path(__file__).resolve().parent
WORK = ROOT / "work"
OUTPUT = ROOT / "output"
WORK.mkdir(exist_ok=True)
OUTPUT.mkdir(exist_ok=True)

ALLOWED_YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "music.youtube.com"}


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def log(message: str) -> None:
    print(message, flush=True)


def progress(stage: str, pct: int, message: str) -> None:
    pct = max(0, min(100, int(pct)))
    log(f"[{pct:3d}%] {stage}: {message}")


def summary(text: str) -> None:
    path = env("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(text.rstrip() + "\n")


def require(name: str) -> str:
    value = env(name)
    if not value:
        raise RuntimeError(f"Secret obrigatório ausente: {name}")
    return value


def validate_youtube_url(url: str) -> None:
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in ALLOWED_YOUTUBE_HOSTS:
        raise RuntimeError("Use um link válido do YouTube.")


def check_configuration() -> None:
    if env("RIGHTS_CONFIRMED").lower() not in {"true", "1", "yes"}:
        raise RuntimeError("Confirme no formulário que você tem direito/permissão para reutilizar o conteúdo.")
    require("CLOUDINARY_CLOUD_NAME")
    require("CLOUDINARY_API_KEY")
    require("CLOUDINARY_API_SECRET")
    require("BUFFER_API_KEY")
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise RuntimeError("FFmpeg/ffprobe não encontrados.")
    progress("Configuração", 1, "Secrets e ferramentas essenciais estão disponíveis")


def human_speed(speed: float | None) -> str:
    if not speed:
        return ""
    return f" · {speed / (1024 * 1024):.1f} MB/s"


def download_youtube(url: str, target_dir: Path) -> tuple[Path, dict]:
    import yt_dlp
    validate_youtube_url(url)
    target_dir.mkdir(parents=True, exist_ok=True)
    template = str(target_dir / "source.%(ext)s")
    last_pct = -1

    def hook(d: dict) -> None:
        nonlocal last_pct
        if d.get("status") == "downloading":
            downloaded = float(d.get("downloaded_bytes") or 0)
            total = float(d.get("total_bytes") or d.get("total_bytes_estimate") or 0)
            if total > 0:
                pct = max(0, min(99, int(downloaded * 100 / total)))
                if pct != last_pct:
                    last_pct = pct
                    eta = d.get("eta")
                    eta_text = f" · ~{int(eta)}s" if isinstance(eta, (int, float)) and eta >= 0 else ""
                    progress("Download", 5 + int(pct * 0.20), f"{pct}%{human_speed(d.get('speed'))}{eta_text}")
        elif d.get("status") == "finished":
            progress("Download", 25, "Concluído; preparando arquivo")

    opts = {
        "format": "bv*[height<=720]+ba/b[height<=720]/best[height<=720]",
        "merge_output_format": "mp4",
        "outtmpl": template,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "restrictfilenames": True,
        "progress_hooks": [hook],
        "concurrent_fragment_downloads": 1,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)

    files = [p for p in target_dir.glob("source.*") if p.suffix.lower() in {".mp4", ".mkv", ".webm", ".mov"}]
    if not files:
        raise RuntimeError("Download terminou, mas nenhum vídeo foi encontrado.")
    return max(files, key=lambda p: p.stat().st_size), info


def probe_duration(path: Path) -> float:
    out = subprocess.check_output([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path)
    ], text=True).strip()
    try:
        return max(0.0, float(out))
    except ValueError:
        return 0.0


def fmt_time(seconds: float) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def transcribe(video: Path, model_name: str, work_dir: Path) -> dict:
    import whisper
    chunks_dir = work_dir / "audio_chunks"
    chunks_dir.mkdir(parents=True, exist_ok=True)
    pattern = chunks_dir / "chunk_%04d.wav"
    subprocess.run([
        "ffmpeg", "-y", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000",
        "-c:a", "pcm_s16le", "-f", "segment", "-segment_time", "60",
        "-reset_timestamps", "1", str(pattern)
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    chunks = sorted(chunks_dir.glob("chunk_*.wav"))
    if not chunks:
        raise RuntimeError("Não foi possível extrair áudio.")
    total = probe_duration(video) or len(chunks) * 60
    progress("Whisper", 28, f"Carregando modelo {model_name}")
    model = whisper.load_model(model_name)

    segments: list[dict] = []
    texts: list[str] = []
    language: str | None = None
    elapsed = 0.0

    for index, chunk in enumerate(chunks, start=1):
        duration = probe_duration(chunk) or min(60.0, max(1.0, total - elapsed))
        stop = threading.Event()

        def heartbeat() -> None:
            started = time.monotonic()
            while not stop.wait(10):
                frac = elapsed / max(total, 1)
                progress("Whisper", 30 + int(frac * 28), f"{fmt_time(elapsed)} / {fmt_time(total)} · bloco {index}/{len(chunks)} · CPU ativa há {int(time.monotonic()-started)}s")

        progress("Whisper", 30 + int((elapsed / max(total, 1)) * 28), f"{fmt_time(elapsed)} / {fmt_time(total)} · bloco {index}/{len(chunks)}")
        thread = threading.Thread(target=heartbeat, daemon=True)
        thread.start()
        try:
            result = model.transcribe(str(chunk), fp16=False, verbose=False, condition_on_previous_text=False)
        finally:
            stop.set()
            thread.join(timeout=1)

        language = language or result.get("language")
        if (result.get("text") or "").strip():
            texts.append(result["text"].strip())
        for seg in result.get("segments", []):
            text = str(seg.get("text") or "").strip()
            if text:
                segments.append({"start": elapsed + float(seg["start"]), "end": elapsed + float(seg["end"]), "text": text})
        elapsed += duration
        chunk.unlink(missing_ok=True)

    progress("Whisper", 60, f"Transcrição concluída · {fmt_time(total)}")
    return {"language": language, "text": " ".join(texts).strip(), "segments": segments}


@dataclass
class Candidate:
    start: float
    end: float
    score: float
    text: str


HOOK_WORDS = {"segredo","verdade","nunca","sempre","porque","como","problema","melhor","pior","incrivel","importante","imagine","descobri","aconteceu","motivo","secret","truth","never","always","why","how","problem","best","worst","important","imagine","discovered","happened"}
EMOTION_WORDS = {"amor","odio","medo","raiva","feliz","triste","chocado","louco","absurdo","love","hate","fear","angry","happy","sad","shocked","crazy","insane"}
FILLERS = {"tipo","assim","né","então","ah","uh","um","like","youknow","basically","actually"}


def words(text: str) -> list[str]:
    return re.findall(r"[\wÀ-ÿ']+", text.lower())


def score_window(text: str, duration: float) -> float:
    ws = words(text)
    if not ws or duration <= 0:
        return 0.0
    count = len(ws)
    unique = len(set(ws)) / max(1, count)
    density = count / duration
    hooks = sum(w in HOOK_WORDS for w in ws)
    emotions = sum(w in EMOTION_WORDS for w in ws)
    opening = sum(w in HOOK_WORDS for w in ws[:24])
    punctuation = text.count("?") + text.count("!")
    fillers = sum(w in FILLERS for w in ws)
    duration_pref = max(0.0, 1.0 - abs(duration - 105.0) / 120.0)
    density_pref = max(0.0, 1.0 - abs(density - 2.2) / 2.2)
    raw = 30*duration_pref + 18*density_pref + 14*min(1.0,hooks/5) + 10*min(1.0,opening/2) + 8*min(1.0,emotions/4) + 8*min(1.0,punctuation/4) + 12*min(1.0,unique/0.65) - 8*min(1.0,fillers/max(1,count)/0.08)
    return round(max(0.0, min(100.0, raw)), 1)


def select_best(segments: list[dict], min_seconds: int, max_seconds: int, count: int) -> list[Candidate]:
    candidates: list[Candidate] = []
    step = max(1, len(segments)//450)
    for i in range(0, len(segments), step):
        start = float(segments[i]["start"])
        parts: list[str] = []
        for j in range(i, len(segments)):
            end = float(segments[j]["end"])
            duration = end-start
            if duration > max_seconds:
                break
            parts.append(str(segments[j]["text"]))
            if duration >= min_seconds:
                text = " ".join(parts).strip()
                candidates.append(Candidate(start,end,score_window(text,duration),text))
                if duration >= min(max_seconds,110):
                    break
    candidates.sort(key=lambda c:c.score, reverse=True)
    selected: list[Candidate] = []
    for c in candidates:
        if all(max(0.0,min(c.end,s.end)-max(c.start,s.start))/max(1.0,min(c.end-c.start,s.end-s.start)) <= 0.25 for s in selected):
            selected.append(c)
        if len(selected) >= count:
            break
    return selected


def extract_json(text: str):
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


def gemini_client():
    key = env("GEMINI_API_KEY")
    if not key:
        return None
    from google import genai
    return genai.Client(api_key=key)


def translate_segments(segments: list[dict], source_language: str | None) -> list[dict]:
    if (source_language or "").lower().startswith("pt"):
        return [dict(s) for s in segments]
    client = gemini_client()
    if not client:
        return [dict(s) for s in segments]
    model = env("GEMINI_MODEL", "gemini-3.8-flash")
    translated = [dict(s) for s in segments]
    for base in range(0, len(segments), 45):
        batch = segments[base:base+45]
        payload = [{"i":base+i,"text":s["text"]} for i,s in enumerate(batch)]
        prompt = 'Traduza para português do Brasil as falas para legendas. Responda SOMENTE JSON no formato [{"i":0,"text":"..."}]. Preserve sentido, tom, nomes e palavrões.\n' + json.dumps(payload, ensure_ascii=False)
        try:
            response = client.models.generate_content(model=model, contents=prompt)
            data = extract_json(response.text or "")
            if isinstance(data, list):
                for item in data:
                    idx = int(item.get("i",-1))
                    if 0 <= idx < len(translated) and item.get("text"):
                        translated[idx]["text"] = str(item["text"]).strip()
        except Exception as exc:
            log(f"Aviso: Gemini não traduziu um lote: {exc}")
    return translated


def fallback_copy(text: str) -> tuple[str,str,list[str]]:
    clean = re.sub(r"\s+", " ", text).strip()
    ws = words(clean)
    stop = {"a","o","as","os","de","da","do","das","dos","e","em","um","uma","para","por","com","que","se","the","an","and","or","to","of","in","on","for","is","are","it","that","this","with","you","i"}
    freq = Counter(w for w in ws if len(w)>3 and w not in stop)
    keywords = [w for w,_ in freq.most_common(5)]
    title = " ".join(clean.split()[:11]).strip(" .,:;!?")[:80] or "Corte em destaque"
    caption = clean[:350]
    cleaned_keywords = [re.sub(r"[^\wÀ-ÿ]", "", k) for k in keywords]
    tags = ["#" + k for k in cleaned_keywords if k] + ["#podcast","#cortes","#tiktok"]
    return title, caption, list(dict.fromkeys(tags))[:8]


def generate_copy(text: str, source_language: str | None) -> tuple[str,str,list[str]]:
    client = gemini_client()
    if not client:
        return fallback_copy(text)
    model = env("GEMINI_MODEL", "gemini-3.8-flash")
    prompt = f'''Crie conteúdo para TikTok em português do Brasil baseado APENAS no trecho abaixo. Responda SOMENTE JSON válido: {{"title":"...","caption":"...","hashtags":["#...", "#..."]}}. Título até 80 caracteres, legenda até 350, 5-8 hashtags, não invente fatos. Idioma fonte: {source_language or 'desconhecido'}.\nTrecho:\n{text[:12000]}'''
    try:
        response = client.models.generate_content(model=model, contents=prompt)
        data = extract_json(response.text or "")
        title = str(data.get("title") or "").strip()[:80]
        caption = str(data.get("caption") or "").strip()[:350]
        hashtags = [str(x).strip() for x in data.get("hashtags",[]) if str(x).strip()][:8]
        if title and caption:
            return title,caption,hashtags
    except Exception as exc:
        log(f"Aviso: Gemini falhou ao gerar texto: {exc}")
    return fallback_copy(text)


def srt_timestamp(seconds: float) -> str:
    seconds=max(0.0,seconds); ms=int(round((seconds-int(seconds))*1000)); whole=int(seconds)
    s=whole%60; m=(whole//60)%60; h=whole//3600
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(segments: list[dict], start: float, end: float, target: Path) -> None:
    rows=[]; idx=1
    for seg in segments:
        s=max(start,float(seg["start"])); e=min(end,float(seg["end"])); text=str(seg["text"]).strip()
        if e>s and text:
            rows += [str(idx), f"{srt_timestamp(s-start)} --> {srt_timestamp(e-start)}", text, ""]
            idx += 1
    target.write_text("\n".join(rows), encoding="utf-8")


def detect_face_center(video: Path, start: float, end: float) -> tuple[int,int,float|None]:
    import cv2
    cap=cv2.VideoCapture(str(video)); width=int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)); height=int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    if width<=0 or height<=0:
        cap.release(); raise RuntimeError("Não foi possível detectar resolução do vídeo.")
    cascade=cv2.CascadeClassifier(cv2.data.haarcascades+"haarcascade_frontalface_default.xml"); centers=[]; duration=max(1.0,end-start)
    try:
        for i in range(10):
            cap.set(cv2.CAP_PROP_POS_MSEC,(start+duration*(i+0.5)/10)*1000); ok,frame=cap.read()
            if not ok: continue
            gray=cv2.cvtColor(frame,cv2.COLOR_BGR2GRAY); faces=cascade.detectMultiScale(gray,scaleFactor=1.1,minNeighbors=4,minSize=(50,50))
            if len(faces):
                x,y,w,h=max(faces,key=lambda f:f[2]*f[3]); centers.append(float(x+w/2))
    finally:
        cap.release()
    centers.sort(); return width,height,centers[len(centers)//2] if centers else None


def render_clip(video: Path, c: Candidate, srt: Path, output: Path, idx: int, total: int) -> None:
    width,height,face_x=detect_face_center(video,c.start,c.end); source_ratio=width/height; target_ratio=9/16
    escaped=str(srt.resolve()).replace("\\","/").replace(":","\\:").replace("'","\\'")
    sub=f"subtitles='{escaped}':force_style='FontName=DejaVu Sans,FontSize=22,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=2,Shadow=0,Alignment=2,MarginV=120'"
    if source_ratio>=target_ratio:
        crop_w=int(height*target_ratio); crop_w-=crop_w%2; x=max(0,int((width-crop_w)/2)) if face_x is None else int(max(0,min(width-crop_w,face_x-crop_w/2)))
        filter_args=["-vf",f"crop={crop_w}:{height}:{x}:0,scale=1080:1920,{sub}"]
    else:
        vf="split=2[fg][bg];[bg]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=20:10[bg2];[fg]scale=1080:1920:force_original_aspect_ratio=decrease[fg2];"+f"[bg2][fg2]overlay=(W-w)/2:(H-h)/2,{sub}[outv]"
        filter_args=["-filter_complex",vf,"-map","[outv]","-map","0:a?"]
    duration=max(0.1,c.end-c.start)
    cmd=["ffmpeg","-y","-ss",f"{c.start:.3f}","-i",str(video),"-t",f"{duration:.3f}"]+filter_args+["-c:v","libx264","-preset","veryfast","-crf","22","-c:a","aac","-b:a","160k","-movflags","+faststart","-pix_fmt","yuv420p","-progress","pipe:1","-nostats","-loglevel","error",str(output)]
    proc=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,bufsize=1); last=-1
    if proc.stdout:
        for raw in proc.stdout:
            line=raw.strip()
            if line.startswith(("out_time_ms=","out_time_us=")):
                try:
                    micros=float(line.split("=",1)[1]); pct=int(max(0,min(99,micros/(duration*1_000_000)*100)))
                    if pct>=last+5:
                        last=pct; overall=65+int(((idx-1)+(pct/100))/max(1,total)*22); progress("Render",overall,f"Corte {idx}/{total}: {pct}%")
                except Exception: pass
    stderr=proc.stderr.read() if proc.stderr else ""; code=proc.wait()
    if code!=0: raise RuntimeError(f"FFmpeg falhou: {stderr[-1500:]}")


def cloudinary_upload(file: Path, clip_number: int) -> tuple[str,str]:
    import cloudinary, cloudinary.uploader
    cloudinary.config(cloud_name=require("CLOUDINARY_CLOUD_NAME"),api_key=require("CLOUDINARY_API_KEY"),api_secret=require("CLOUDINARY_API_SECRET"),secure=True)
    folder=env("CLOUDINARY_FOLDER","autoclips").strip("/") or "autoclips"; public_id=f"{int(time.time())}_clip_{clip_number}"
    opts={"resource_type":"video","folder":folder,"public_id":public_id,"overwrite":False,"unique_filename":False,"use_filename":False}
    result=cloudinary.uploader.upload_large(str(file),chunk_size=6_000_000,**opts) if file.stat().st_size>95*1024*1024 else cloudinary.uploader.upload(str(file),**opts)
    url=str(result.get("secure_url") or "").strip(); pid=str(result.get("public_id") or "").strip()
    if not url: raise RuntimeError("Cloudinary não devolveu secure_url.")
    return url,pid


class BufferClient:
    def __init__(self): self.api_key=require("BUFFER_API_KEY")
    def call(self,query:str,variables:dict|None=None)->dict:
        r=requests.post("https://api.buffer.com",headers={"Authorization":f"Bearer {self.api_key}","Content-Type":"application/json"},json={"query":query,"variables":variables or {}},timeout=60); r.raise_for_status(); data=r.json()
        if data.get("errors"): raise RuntimeError(data["errors"][0].get("message","Erro GraphQL no Buffer"))
        return data.get("data") or {}
    def channel_id(self)->str:
        configured=env("BUFFER_CHANNEL_ID")
        if configured: return configured
        orgs=self.call("query { account { organizations { id name } } }").get("account",{}).get("organizations",[])
        for org in orgs:
            q="""query Channels($org: OrganizationId!) { channels(input: { organizationId: $org }) { id name displayName service isQueuePaused } }"""
            for channel in self.call(q,{"org":org["id"]}).get("channels",[]):
                if str(channel.get("service","")).lower()=="tiktok": return str(channel["id"])
        raise RuntimeError("Nenhum canal TikTok conectado foi encontrado no Buffer.")
    def add_video_to_queue(self,video_url:str,text:str)->str:
        q="""mutation CreateVideo($input: CreatePostInput!) { createPost(input: $input) { ... on PostActionSuccess { post { id dueAt } } ... on MutationError { message } } }"""
        variables={"input":{"text":text,"channelId":self.channel_id(),"schedulingType":"automatic","mode":"addToQueue","assets":[{"video":{"url":video_url,"metadata":{"thumbnailOffset":2000}}}]}}
        result=self.call(q,variables).get("createPost") or {}
        if result.get("message"): raise RuntimeError(result["message"])
        post=result.get("post") or {}
        if not post.get("id"): raise RuntimeError("Buffer não devolveu ID da publicação.")
        return str(post["id"])


def run(url:str,clips_count:int,min_seconds:int,max_seconds:int,whisper_model:str)->None:
    check_configuration(); shutil.rmtree(WORK,ignore_errors=True); shutil.rmtree(OUTPUT,ignore_errors=True); WORK.mkdir(exist_ok=True); OUTPUT.mkdir(exist_ok=True)
    progress("Início",2,"Baixando fonte"); video,info=download_youtube(url,WORK); title=str(info.get("title") or "Vídeo do YouTube"); summary(f"# AutoClip\n\n**Fonte:** {title}\n")
    transcript=transcribe(video,whisper_model,WORK); segments=transcript.get("segments") or []
    if not segments: raise RuntimeError("Whisper não encontrou fala suficiente.")
    progress("Análise",61,"Traduzindo legendas quando necessário"); subtitle_segments=translate_segments(segments,transcript.get("language")); progress("Análise",64,"Selecionando os melhores trechos")
    candidates=select_best(segments,min_seconds,max_seconds,clips_count)
    if not candidates: raise RuntimeError("Nenhum trecho adequado foi encontrado.")
    buffer=BufferClient(); results=[]; total=len(candidates)
    for idx,c in enumerate(candidates,start=1):
        progress("Corte",65+int((idx-1)/total*25),f"Preparando {idx}/{total} · score {c.score}"); srt=WORK/f"clip_{idx}.srt"; output=OUTPUT/f"clip_{idx}.mp4"; write_srt(subtitle_segments,c.start,c.end,srt); render_clip(video,c,srt,output,idx,total)
        post_title,caption,hashtags=generate_copy(c.text,transcript.get("language")); progress("Cloudinary",88+int((idx-1)/total*6),f"Enviando corte {idx}/{total}"); cloud_url,cloud_id=cloudinary_upload(output,idx)
        text=f"{caption}\n\n{' '.join(hashtags)}".strip(); progress("Buffer",94+int((idx-1)/total*5),f"Adicionando corte {idx}/{total} à fila do TikTok"); buffer_id=buffer.add_video_to_queue(cloud_url,text)
        results.append({"clip":idx,"score":c.score,"title":post_title,"cloudinary_url":cloud_url,"cloudinary_id":cloud_id,"buffer_post_id":buffer_id}); output.unlink(missing_ok=True)
    progress("Concluído",100,f"{len(results)} corte(s) enviados ao Buffer"); summary("## Resultado\n")
    for r in results: summary(f"- **Corte {r['clip']}** — score {r['score']} — Buffer `{r['buffer_post_id']}` — {r['cloudinary_url']}")
    summary("\nOs cortes foram adicionados à fila do Buffer. Configure no canal TikTok do Buffer os horários 12:00, 18:00 e 22:00 para que ele distribua automaticamente os posts nesses horários.\n")


def main()->None:
    parser=argparse.ArgumentParser(description="AutoClip GitHub Actions"); parser.add_argument("--check",action="store_true"); parser.add_argument("--url"); parser.add_argument("--clips",type=int,default=3); parser.add_argument("--min-seconds",type=int,default=60); parser.add_argument("--max-seconds",type=int,default=180); parser.add_argument("--whisper-model",default="tiny"); args=parser.parse_args()
    try:
        if args.check: check_configuration(); return
        if not args.url: parser.error("--url é obrigatório")
        run(args.url,max(1,min(3,args.clips)),max(20,args.min_seconds),max(args.min_seconds,args.max_seconds),args.whisper_model)
    except Exception as exc:
        summary(f"\n## Falha\n\n`{type(exc).__name__}: {exc}`\n"); log(f"ERRO: {type(exc).__name__}: {exc}"); raise
    finally:
        shutil.rmtree(WORK,ignore_errors=True); shutil.rmtree(OUTPUT,ignore_errors=True)


if __name__=="__main__": main()
