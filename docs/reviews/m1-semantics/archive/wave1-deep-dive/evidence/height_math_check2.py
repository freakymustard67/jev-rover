#!/usr/bin/env python3
"""Part-B realism check 2:
 a) honest DETECTOR bbox on a cylinder (dense silhouette) -> chain anchor/H/top
 b) conditioning: bbox pixel height vs camera tilt and distance
 c) recovering the camera ground point C_xy from >=2 standing-object detections
    (no intrinsics needed) -- the self-calibration fallback
Writes nothing.
"""
import numpy as np
import cv2

ROOM = (6.4, 3.6); W, H_IMG = 1280, 720

def make_cam(center, tilt, f, w=W, h=H_IMG):
    K = np.array([[f,0,w/2],[0,f,h/2],[0,0,1.]])
    th=np.deg2rad(tilt)
    zc=np.array([np.sin(th),0,-np.cos(th)]); xc=np.array([np.cos(th),0,np.sin(th)])
    yc=np.cross(zc,xc); R=np.stack([xc,yc,zc],1); C=np.asarray(center,float)
    t=-R.T@C; return K,R,C,K@np.hstack([R.T,t[:,None]])

def project(P, pts):
    pts=np.asarray(pts,float).reshape(-1,3)
    hom=(P@np.hstack([pts,np.ones((len(pts),1))]).T).T
    return hom[:,:2]/hom[:,2:3], hom[:,2]

def fit_H(P, corners):
    ip,_=project(P,[(x,y,0.) for x,y in corners])
    return cv2.getPerspectiveTransform(np.float32(ip), np.asarray(corners,np.float32))

def Himg(Hm,p):
    v=Hm@np.array([p[0],p[1],1.]); return v[:2]/v[2]

CORNERS=[(0.3,0.3),(6.1,0.3),(6.1,3.3),(0.3,3.3)]

def cylinder_bbox(P, xy, h, r=0.033, n=72):
    """dense sample of cylinder surface (top rim, bottom rim, sides) -> silhouette bbox"""
    ang=np.linspace(0,2*np.pi,n,endpoint=False)
    pts=[]
    for z in np.linspace(0,h,7):
        for a in ang:
            pts.append((xy[0]+r*np.cos(a), xy[1]+r*np.sin(a), z))
    px,ok=project(P,pts); px=px[ok>0]
    return px[:,0].min(), px[:,1].min(), px[:,0].max(), px[:,1].max()

def main():
    print("=== a) detector bbox on a real cylinder (r=0.033 m, h=0.25 m) ===")
    for tilt in (0.0, 25.0, 45.0):
        Zc = 2.8
        K,R,C,P = make_cam((3.2,1.8,Zc), tilt, 420.)
        Hm = fit_H(P, CORNERS)
        for (x,y) in [(4.0,2.0),(2.2,1.2),(4.8,2.8)]:
            x0,y0,x1,y1 = cylinder_bbox(P,(x,y),0.25)
            anchor=np.array([(x0+x1)/2, y1])          # repo's bbox_bottom_center
            top=   np.array([(x0+x1)/2, y0])
            Pb=Himg(Hm,anchor); Pt=Himg(Hm,top)
            d=float(np.hypot(*(Pb-np.array(C[:2])))); D=float(np.linalg.norm(Pt-Pb))
            h_est=Zc*D/(d+D)
            print(f" tilt {tilt:4.0f} deg pos ({x},{y}) bbox y0..y1={y0:6.1f}..{y1:6.1f} "
                  f"({y1-y0:4.1f} px) anchor err {100*np.hypot(*(Pb-[x,y])):5.2f} cm -> "
                  f"h_est {h_est:.3f} m (err {100*(h_est-0.25):+5.2f} cm)")

    print("\n=== b) conditioning: bbox pixel height for h=0.25 m, tilt=25 deg ===")
    K,R,C,P = make_cam((3.2,1.8,2.8), 25., 420.); Hm=fit_H(P,CORNERS)
    for (x,y) in [(3.4,1.9),(4.2,2.1),(2.4,1.5),(5.2,2.6)]:
        x0,y0,x1,y1=cylinder_bbox(P,(x,y),0.25)
        d=float(np.hypot(x-3.2,y-1.8))
        print(f"  pos ({x},{y}) d={d:.2f} m: bbox {y1-y0:5.1f} px  "
              f"-> 1 px ~ {0.25/(y1-y0):.4f} m of h")
    print("\n  for comparison, nadir camera (tilt 0):" )
    K,R,C,P = make_cam((3.2,1.8,2.8), 0., 420.); Hm=fit_H(P,CORNERS)
    for (x,y) in [(3.4,1.9),(4.2,2.1),(5.2,2.6)]:
        x0,y0,x1,y1=cylinder_bbox(P,(x,y),0.25)
        d=float(np.hypot(x-3.2,y-1.8))
        print(f"  pos ({x},{y}) d={d:.2f} m: bbox {y1-y0:5.1f} px  "
              f"-> 1 px ~ {0.25/(y1-y0):.4f} m of h")

    print("\n=== c) camera ground point from >=2 detections (no intrinsics) ===")
    K,R,C,P = make_cam((3.2,1.8,2.8), 25., 420.); Hm=fit_H(P,CORNERS)
    lines=[]
    for (x,y,h) in [(2.0,1.0,0.20),(4.4,2.6,0.30),(5.4,0.9,0.25)]:
        x0,y0,x1,y1=cylinder_bbox(P,(x,y),h)
        Pb=Himg(Hm,np.array([(x0+x1)/2,y1])); Pt=Himg(Hm,np.array([(x0+x1)/2,y0]))
        u=Pt-Pb; u=u/np.linalg.norm(u)     # direction away from camera
        lines.append((Pb,u))
        print(f"  offset (unknown h is fine): line through {np.round(Pb,2).tolist()} dir {np.round(u,3).tolist()}")
    # least-squares intersection of lines Pb + t*(-u)
    A=[];b=[]
    for Pb,u in lines:
        n=np.array([[0,-1],[1,0]])@u       # normal to the line
        A.append(n); b.append(n@Pb)
    A=np.array(A); b=np.array(b)
    xy_ls,*_=np.linalg.lstsq(A,b,rcond=None)
    print(f"  least-squares C_xy = {np.round(xy_ls,3).tolist()}  (true {C[:2].tolist()}) "
          f"err {100*np.linalg.norm(xy_ls-C[:2]):.2f} cm")
    print("  with 3 noisy detections (+-2 px top, +-1 px bottom):")
    rng=np.random.default_rng(3)
    for trial in range(3):
        A=[];b=[]
        for (x,y,h) in [(2.0,1.0,0.20),(4.4,2.6,0.30),(5.4,0.9,0.25)]:
            x0,y0,x1,y1=cylinder_bbox(P,(x,y),h)
            anchor=np.array([(x0+x1)/2,y1])+rng.normal(0,1,2)
            top=   np.array([(x0+x1)/2,y0])+rng.normal(0,2,2)
            Pb=Himg(Hm,anchor); Pt=Himg(Hm,top)
            u=Pt-Pb; u=u/np.linalg.norm(u)
            n=np.array([[0,-1],[1,0]])@u; A.append(n); b.append(n@Pb)
        A=np.array(A); b=np.array(b)
        xy_ls,*_=np.linalg.lstsq(A,b,rcond=None)
        print(f"   trial {trial}: C_xy err {100*np.linalg.norm(xy_ls-C[:2]):.2f} cm")

if __name__=="__main__":
    main()
