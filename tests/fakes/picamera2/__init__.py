import threading,time,json,os
EVENTS=os.environ.get("FAKECAM_LOG","/dev/null")
def ev(*a):
    with open(EVENTS,"a") as f: f.write(json.dumps(a,default=str)+"\n")
class Picamera2:
    camera_controls={k:None for k in ["Brightness","Contrast","Saturation","Sharpness","ExposureValue","AwbMode","AeExposureMode","NoiseReductionMode","FrameDurationLimits"]}
    def __init__(s): s.t=None;s.run=False;s.fd=1e6/12;s.cfg=None
    def create_video_configuration(s,main,transform): return {"size":main["size"],"transform":repr(transform)}
    def configure(s,c): s.cfg=c;ev("configure",c)
    def set_controls(s,c):
        ev("set_controls",c)
        if "FrameDurationLimits" in c: s.fd=c["FrameDurationLimits"][0]
    def start_recording(s,enc,out):
        ev("start",enc.q); s.run=True
        def loop():
            i=0
            while s.run:
                out.f.write(b"\xff\xd8"+i.to_bytes(4,"big")+b"\0"*3000+b"\xff\xd9");i+=1;time.sleep(s.fd/1e6)
        s.t=threading.Thread(target=loop,daemon=True);s.t.start()
    def stop_recording(s):
        ev("stop"); s.run=False
        if s.t: s.t.join()
