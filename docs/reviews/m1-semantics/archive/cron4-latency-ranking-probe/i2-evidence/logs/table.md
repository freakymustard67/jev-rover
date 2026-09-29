| scenario | class | truth | master | PR | proto | |d| | dL | dC | probe px |
|---|---|---|---|---|---|---|---|---|---|
| floor_only | floor | F | F | F | F | 0.0 | 0.0 | 0.0 | 1000,556 |
| mat_thing | floor | F | **T** | **T** | F | 84.02 | 67.0 | 50.7 | 600,516 |
| mat_itself | floor | F | **T** | **T** | F | 84.02 | 67.0 | 50.7 | 600,546 |
| mat_edge_floor | floor | F | F | F | F | 0.0 | 0.0 | 0.0 | 600,536 |
| shadow_mild | floor | F | F | F | F | 20.0 | 20.0 | 0.0 | 1000,556 |
| shadow_deep | floor | F | **T** | **T** | F | 40.0 | 40.0 | 0.0 | 1000,556 |
| shadow_vdeep | floor | F | **T** | **T** | F | 70.0 | 70.0 | 0.0 | 1000,556 |
| shadow_contact | floor | F | **T** | **T** | F | 45.01 | 45.0 | 1.0 | 1000,556 |
| shadow_contact_narrow | floor | F | **T** | **T** | F | 45.01 | 45.0 | 1.0 | 1000,556 |
| dark_card_wide | floor | F | **T** | **T** | **T** | 103.16 | 103.0 | 5.83 | 1000,556 |
| occluder_narrow | floor | F | **T** | **T** | **T** | 103.16 | 103.0 | 5.83 | 1000,556 |
| dark_mat_thing | floor | F | **T** | **T** | F | 55.31 | 55.0 | 5.83 | 960,436 |
| tight_bbox_speckle | floor | F | **T** | **T** | **T** | 126.29 | 126.0 | 8.49 | 1000,556 |
| on_table | elevated | T | **T** | **T** | **T** | 78.86 | 73.0 | 29.83 | 626,682 |
| edge_polygon | unverifiable | T | **T** | **T** | **T** | 0.0 | 0.0 | 0.0 | 120,692 |

master: FP=10 FN=0 fp_cases=['mat_thing', 'mat_itself', 'shadow_deep', 'shadow_vdeep', 'shadow_contact', 'shadow_contact_narrow', 'dark_card_wide', 'occluder_narrow', 'dark_mat_thing', 'tight_bbox_speckle']
   flake floor_only: noise-only 0/176 injected 24/24 total 24/200
   flake mat_thing: noise-only 176/176 injected 24/24 total 200/200
   flake shadow_contact: noise-only 176/176 injected 24/24 total 200/200
   flake tight_bbox_speckle: noise-only 176/176 injected 24/24 total 200/200
pr: FP=10 FN=0 fp_cases=['mat_thing', 'mat_itself', 'shadow_deep', 'shadow_vdeep', 'shadow_contact', 'shadow_contact_narrow', 'dark_card_wide', 'occluder_narrow', 'dark_mat_thing', 'tight_bbox_speckle']
   flake floor_only: noise-only 0/176 injected 0/24 total 0/200
   flake mat_thing: noise-only 176/176 injected 24/24 total 200/200
   flake shadow_contact: noise-only 176/176 injected 24/24 total 200/200
   flake tight_bbox_speckle: noise-only 176/176 injected 24/24 total 200/200
proto: FP=3 FN=0 fp_cases=['dark_card_wide', 'occluder_narrow', 'tight_bbox_speckle']
   flake floor_only: noise-only 0/176 injected 0/24 total 0/200
   flake mat_thing: noise-only 0/176 injected 0/24 total 0/200
   flake shadow_contact: noise-only 0/176 injected 0/24 total 0/200
   flake tight_bbox_speckle: noise-only 176/176 injected 24/24 total 200/200
