#!/usr/bin/env python3
"""Part-B realism check 3: (c-fixed) C_xy self-calibration with rods; anchor-pixel
sensitivity; plausibility window for the height signal. Writes nothing."""
import numpy as np, cv2
ROOM=(6.4,3.6); W,H_IMG=1280,720

def make_cam(center,tilt,f,w=W,h=H_IMG):
    K=np.array([[f,0,w/2],[0,f,h/2],[0,0,1.]])
    th=np.deg2rad(tilt); zc=np.array([np.sin(th),0,-np.cos(th)]); xc=np.array([np.cos(th),0,np.sin(th)])
    yc=np.cross(zc,xc); R=np.stack([xc,yc,zc],1); C=np.asarray(center,float); t=-R.T@C
    return K,R,C,K@np.hstack([R.T,t[:,None]])

def project(P,pts):
    pts=np.asarray(pts,float).reshape(-1,3); hom=(P@np.hstack([pts,np.ones((len(pts),1))]).T).T
    return hom[:,:2]/hom[:,2:3], hom[:,2]

def fit_H(P,corners):
    ip,_=project(P,[(x,y,0.) for x,y in corners]); return cv2.getPerspectiveTransform(np.float32(ip),np.asarray(corners,np.float32))

def Himg(Hm,p):
    v=Hm@np.array([p[0],p[1],1.]); return v[:2]/v[2]

CORNERS=[(0.3,0.3),(6.1,0.3),(6.1,3.3),(0.3,3.3)]
Zc=2.8; C=(3.2,1.8,Zc)
K,R,C,P=make_cam(C,0.,340.); Hm=fit_H(P,CORNERS)

print("=== (c-fixed) C_xy from >=2 ROD detections, line intersection ===")
rng=np.random.default_rng(5)
def rod_line(x,y,h,noise_top=0.,noise_bot=0.):
    bp,_=project(P,[(x,y,0.)]); tp,_=project(P,[(x,y,h)])
    a=bp[0]+rng.normal(0,noise_bot,2); t=tp[0]+rng.normal(0,noise_top,2)
    Pb=Himg(Hm,a); Pt=Himg(Hm,t); u=Pt-Pb; return Pb,u/np.linalg.norm(u)

for label,nt,nb in [("exact",0.,0.),("top +-2px, bottom +-1px",2.,1.),("top +-3px, bottom +-2px",3.,2.)]:
    errs=[]
    for trial in range(200):
        A=[];b=[]
        for (x,y,h) in [(2.0,1.0,0.20),(4.4,2.6,0.30),(5.4,0.9,0.25)]:
            Pb,u=rod_line(x,y,h,nt,nb)
            n=np.array([[0,-1],[1,0]])@u; A.append(n); b.append(n@Pb)
        A=np.array(A);b=np.array(b); xy,*_=np.linalg.lstsq(A,b,rcond=None)
        errs.append(np.linalg.norm(xy-C[:2])*100)
    print(f"  {label:26s}: C_xy err p50={np.percentile(errs,50):6.2f} cm  p90={np.percentile(errs,90):6.2f} cm")
print("  (degrades with a small baseline: all 3 rods at d~1-2.5 m of a 6.4x3.6 m room)")

print("\n=== anchor (bottom-edge) pixel sensitivity, rod h=0.25 at (4.0,2.0) ===")
bp,_=project(P,[(4.,2.,0.)]); tp,_=project(P,[(4.,2.,0.25)])
Pb0=Himg(Hm,bp[0]); Pt0=Himg(Hm,tp[0]); d=float(np.hypot(*(Pb0-C[:2])))
for dbp in [(1,0),(-1,0),(0,1),(0,-1),(2,0)]:
    Pb=Himg(Hm,bp[0]+np.array(dbp,float)); D=float(np.linalg.norm(Pt0-Pb))
    dd=float(np.hypot(*(Pb-np.array(C[:2]))))
    print(f"  bottom px {dbp}: h_est {Zc*D/(dd+D):.4f} (dh {100*(Zc*D/(dd+D)-0.25):+.2f} cm)")

print("\n=== plausibility window: h_est for h_true in the bottle/box range, 3 positions ===")
for (x,y) in [(4.0,2.0),(2.0,1.2),(5.4,2.8)]:
    row=[]
    for h in (0.0,0.10,0.20,0.30,0.60,1.00):
        bp,_=project(P,[(x,y,0.)]); tp,_=project(P,[(x,y,h)])
        Pb=Himg(Hm,bp[0]); Pt=Himg(Hm,tp[0]); D=float(np.linalg.norm(Pt-Pb))
        d=float(np.hypot(*(Pb-np.array(C[:2])))); row.append(f"{Zc*D/(d+D):.2f}")
    print(f"  ({x},{y}): h_true 0/0.10/0.20/0.30/0.60/1.00 -> h_est {' / '.join(row)}")
print("\n  note: h_est is monotone in the bbox extent, so the signal 'bbox taller than a")
print("  0.3 m object at this spot would produce' separates floor objects from elevated ones")
print("  only if the model error (feet/shape) is below the gap; measured model error on a")
print("  real cylinder: 0.7-7.5 cm (see check 2a).")
