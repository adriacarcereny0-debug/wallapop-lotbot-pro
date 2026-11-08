# Genera la banda sonora (40 s): tic-tac tenso en el problema, whoosh en la transición,
# beat optimista desde la solución y "pings" en los momentos clave.
import numpy as np, wave
SR=44100; D=40; t=np.arange(SR*D)/SR; out=np.zeros_like(t)
def add(sig,at):
    i=int(at*SR); n=min(len(sig),len(out)-i); out[i:i+n]+=sig[:n]
def env(n,a=.005,r=.2):
    x=np.arange(n)/SR; return np.minimum(1,x/a)*np.exp(-x/r)
def tone(f,d,a=.005,r=.2,typ='sin'):
    n=int(d*SR); x=np.arange(n)/SR; w=np.sin(2*np.pi*f*x) if typ=='sin' else np.sign(np.sin(2*np.pi*f*x))*.5
    return w*env(n,a,r)
def kick():
    n=int(.35*SR); x=np.arange(n)/SR; f=50+120*np.exp(-x*30); return np.sin(2*np.pi*np.cumsum(f)/SR)*np.exp(-x*9)
def hat():
    n=int(.06*SR); return np.random.randn(n)*np.exp(-np.arange(n)/SR*70)*.25
def clap():
    n=int(.18*SR); return np.random.randn(n)*np.exp(-np.arange(n)/SR*25)*.35
rng=np.random.default_rng(1); np.random.seed(1)
# hook: impacto + latido
add(kick()*1.2,0.05); add(tone(55,1.5,.01,.6)*.6,0.05)
for k in range(8): add(tone(1800 if k%2 else 1400,.04,.001,.015)*.25,0.5+k*.45)
add(kick(),2.1); add(tone(110,0.8,.01,.3,'sq')*.25,2.1)
# problema: tic-tac + bajo grave lento
for k in range(int((12-4)/0.5)): add(tone(2000 if k%2 else 1500,.04,.001,.012)*.3,4+k*.5)
for k,f in enumerate([65,62,58,55]): add(tone(f,2,.05,1.2)*.5,4+k*2)
for k in range(10): add(tone(900,.05,.001,.02)*.15,10.6+k*.1)  # avance rápido
# whoosh 11.9-12.6
n=int(.8*SR); x=np.arange(n)/SR; wn=np.random.randn(n); wn=np.convolve(wn,np.ones(30)/30,'same')
add(wn*np.sin(np.pi*x/.8)**2*1.2,11.85)
add(tone(880,1.2,.005,.5)*.3,13.4); add(tone(1318,1.2,.005,.5)*.2,13.45)
# beat optimista 13.4-40 (116 bpm)
bpm=116; b=60/bpm; chords=[[261.6,329.6,392],[220,261.6,329.6],[174.6,220,261.6],[196,246.9,293.7]]
s=13.4; k=0
while s+k*b<39.2:
    tt=s+k*b; add(kick()*.9,tt); add(hat(),tt+b/2)
    if k%2==1: add(clap(),tt)
    if k%4==0:
        ch=chords[(k//4)%4]
        for f in ch: add(tone(f,b*4,.02,1.0)*.09,tt); add(tone(f/2,b*4,.02,1.2,'sq')*.03,tt)
        add(tone(ch[0]/4,b*3.5,.01,.8)*.35,tt)
    if k%2==0: add(tone(chords[(k//4)%4][(k//2)%3]*2,b/2,.003,.08)*.08,tt+b*.75)
    k+=1
# pings: confirmaciones y tarjetas
for at in [17.2,21.45,24.6,25.4,26.65,27.9,29.15,30.6,35.2]:
    add(tone(1568,.4,.002,.12)*.22,at); add(tone(2093,.4,.002,.12)*.15,at+.06)
fade=np.ones_like(t); fe=t>38.5; fade[fe]=np.maximum(0,1-(t[fe]-38.5)/1.5); out*=fade
out=np.tanh(out*1.1)*.85
pcm=(np.stack([out,out],1)*32767).astype('<i2')
with wave.open('musica.wav','wb') as w: w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR); w.writeframes(pcm.tobytes())
