class Transform:
    def __init__(s,hflip=False,vflip=False): s.hflip,s.vflip=hflip,vflip
    def __repr__(s): return f"T(h={s.hflip},v={s.vflip})"
class _E:
    def __init__(s,*names):
        for i,n in enumerate(names): setattr(s,n,f"{type(s).__name__}.{n}")
class AwbModeEnum(_E):pass
class AeExposureModeEnum(_E):pass
class AfModeEnum(_E):pass
class NoiseReductionModeEnum(_E):pass
class controls:
    AwbModeEnum=AwbModeEnum("Auto","Incandescent","Tungsten","Fluorescent","Indoor","Daylight","Cloudy")
    AeExposureModeEnum=AeExposureModeEnum("Normal","Short","Long")
    AfModeEnum=AfModeEnum("Manual","Auto","Continuous")
    class draft: NoiseReductionModeEnum=NoiseReductionModeEnum("Off","Fast","HighQuality")
