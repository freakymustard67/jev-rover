Title: Cover glass guidelines for the single-zone Time‑of‑Flight sensor - Application note

URL Source: https://www.st.com/resource/en/application_note/an5231-cover-window-guidelines-for-the-vl53l1x-longdistance-ranging-timeofflight-sensor-stmicroelectronics.pdf

Number of Pages: 21

Markdown Content:
Introduction 

The aim of this application note is to provide guidelines for industrial designers on how to improve cover glass quality. This document gives ST’s recommendations on cover glass selection and details the design requirements for optimizing the system for single-zone sensors. 

Figure 1.  Example of a single-zone device: VL53L1X 

# Cover glass guidelines for the single-zone Time ‑of ‑Flight sensor 

# AN5231 

## Application note     

> AN5231 -Rev 2 -May 2026
> For further information, contact your local STMicroelectronics sales office. www.st.com

# 1 Acronyms and abbreviations 

The main acronyms and abbreviations used in this document are listed below. 

Table 1.  Acronyms and abbreviations 

Acronym/abbreviation  Definition 

AFC  Antifingerprint coating 

ARC  Antireflective coating 

cps  Count per second 

ID  Industrial design 

IR  Infrared 

PMMA  Polymethyl methacrylate 

SNR  Signal-to-noise ratio 

SPAD  Single photon avalanche diode 

ToF  Time-of-Flight 

## AN5231 

Acronyms and abbreviations    

> AN5231 -Rev 2 page 2/21

# 2 General information 

The module is typically used with a window covering. This "cover glass" serves two main purposes: 

• It provides physical protection for the module, including dust ingress prevention 

• It provides additional optical filtering for the module 

This application note presents information which allows the user to select the performance level of the cover glass and the best topology for his use case. 

There are two options for building the cover glass: 

• Two-glass adapter, also called cover glass with barrier:  This option separates the emitter and receiver optical paths. It is the best solution as it provides high-ranging performances and limits calibration requirements. 

• Plain cover glass : When the above solution is not possible (for aesthetic reasons or when the material used cannot be split) a plain cover glass can be selected. 

Figure 2.  Two-glass adapter (left) and plain cover glass (right) 

## AN5231    

> General information
> AN5231 -Rev 2 page 3/21

## 2.1  System crosstalk 

In electronics, crosstalk is any phenomenon by which a signal transmitted on one circuit or channel of a transmission system creates an undesired effect in another circuit or channel. Crosstalk is usually caused by undesired capacitive, inductive, or conductive coupling from one circuit or channel to another. In imaging, crosstalk is the interference caused by the ranging laser light that is reflected by a cover glass and not the intended target, which therefore distorts ToF data. 

When using plain glass, there are two direct paths for the photons between the emitter and the receiver (see red curves in figure below). 

• Path 1: The bottom surface of the cover glass which partially reflects the beam. This contributes to crosstalk. The bigger the air gap, the higher the crosstalk. 

• Path 2: The top surface of the cover glass also reflects light towards the return receiver. It may bounce multiple times inside the cover glass before reaching the receiver. The thicker the cover glass, the higher its contribution to crosstalk. 

These two paths lead the receiver to detect photons with a very short optical path. They degrade the ranging distance measured by the module. 

Figure 3.  Crosstalk paths (red lines) 

## AN5231    

> General information
> AN5231 -Rev 2 page 4/21

# 3 Crosstalk contributing factors and recommendations to reduce it 

## 3.1  Optical transmission 

The module is based on an optical technology using 940 nm wavelength. It is then mandatory to optimize photon transmission through the cover glass in the 930-950 nm bandwidth. It is required to have the optical transmission of the cover glass higher than 87% in this bandwidth. 

## 3.2  Haze 

ToF is an optical technology and the optical distortion impacts system performance. Haze is defined as the percentage of light that, when passing through a certain material, deviates from the incident beam, on average, by an angle greater than 2.5 degrees. Refer to  ASTM D 1003, Standard Test Method for Haze and Luminous 

Transmittance of Transparent Plastics, American Society for Testing and Materials, Philadelphia, PA, 1994 .

Haze is the best way to measure the different impacting parameters including: 

• embedded particles or trapped air 

• material manufacturing procedures 

• scattering 

• surface roughness 

• surface thickness 

It is recommended that haze should not exceed 2% of the total light emitted (1%, 940 nm IR). 

Figure 4.  Example of optical distortion due to rough ink 

## AN5231 

Crosstalk contributing factors and recommendations to reduce it    

> AN5231 -Rev 2 page 5/21

## 3.3  Smudge 

Smudge is the term used to describe dirt on the cover glass. Smudge can be caused by fingerprints, grease, dust, or anything that lies on top of the cover glass and optically interferes with the sensor. 

Any protective film/coating with high surface tensile strength on top of the cover glass may be considered sensitive for ToF technology. Such materials can affect optical scattering. 

Smudge sensitivity depends on the cover glass material and manufacturing process. ST recommends that cover glass smudge sensitivity be assessed. 

Note:  Smudge increases system crosstalk. If the system has a clean cover glass and high crosstalk, it is likely that the smudge contribution to crosstalk is also high. 

## 3.4  Cover glass tilt and surface parallelism 

The cover glass top and bottom surfaces must both be parallel to the device surface. Ideally, avoid cover glass tilt to reduce the crosstalk signals. 

Minimum crosstalk is achieved when the angle between the module and the cover glass is less than 10°. 

If a higher angle is required, the module is still functional, but the ranging performance may be affected. 

Figure 5.  Example of cover glass tilt requirement with VL53L1X 

## 3.5  Cover glass thickness 

The thickness of the cover glass contributes to crosstalk by reflecting photons towards the receiver. 

A thin cover glass means that the beam bounces internally many times before reaching the receiver. As the signal is attenuated at each bounce, the crosstalk is finally low. 

## AN5231    

> Crosstalk contributing factors and recommendations to reduce it
> AN5231 -Rev 2 page 6/21

## 3.6  Air gap 

As shown in the figure below, the distance between the top of the cap of the module and the bottom surface of the cover glass is the air gap. 

Figure 6.  Example of air gap with VL53L1X 

As the air gap increases, the amount of crosstalk also increases. 

For optimum ranging performance, the air gap should be kept as small as possible (see figure below which shows an example of crosstalk variation versus air gap size. 

Figure 7.  Example of crosstalk variation vs. air gap size 

## AN5231    

> Crosstalk contributing factors and recommendations to reduce it
> AN5231 -Rev 2 page 7/21

The ID rules of thumb listed below can be applied: 

• For fast and high-accuracy long ranging (>2000 mm): 

– Total air gap and cover glass thickness = 1 mm maximum 

– Cover glass thickness <0.6 mm and air gap <0.4 mm 

– Gasket and/or light blocker enhances performance (see  Section 5.3:  Gaskets )

• For sub 1000 mm ranging: 

– Total air gap and cover glass thickness = 2 mm maximum 

– Cover glass thickness <1.5 mm and air gap <0.5 mm 

– Gasket is required 

• For sub 600 mm ranging (no specific accuracy requirement) with an air gap and cover glass thickness >2.0 mm: 

– A dedicated ID study is required to optimize ranging and system performance 

For all use cases above, the assembly tolerances have to be accounted for when considering air gap and cover glass thickness. 

Note:  The variation in the amount of crosstalk compensation required can be important if the air gap is likely to change throughout the lifetime of the system. 

## 3.7  External contributors 

Any external source of 930-950 nm light may impact the performance of the module. Such sources of light increase the ambient light and so impact the SNR of each measurement. Consequently, the user should ensure that the design is set up to minimize optical interference from other IR sensors emitting in the same wavelength, or, even better, that the other sensors are not activated at the same time as the module. 

## AN5231    

> Crosstalk contributing factors and recommendations to reduce it
> AN5231 -Rev 2 page 8/21

# 4 Crosstalk compensation 

## 4.1  Crosstalk impact on ranging 

Crosstalk compensation is a feature that is embedded in the firmware or the driver of ToF sensors. It allows compensation of the crosstalk effect, based on characterization results and calibration data. 

The procedure for crosstalk calibration or characterization is detailed in the product's user manual. In general, the lower the crosstalk, the easier it is to compensate. Additionally, the less variation in crosstalk due to smudge or haze, the easier it is to compensate in the field. A cover glass with poor quality design or manufacture increases the crosstalk level. In the same way, smudge or haze on the top of the cover glass degrade the target vs crosstalk signal ratio. 

Depending on the type of data processing (sigma delta or histogram sensors), the effect of crosstalk will be different. 

Note:  All distances are in mm in the following graphs. 

4.1.1  Sigma delta sensors (VL53L0X, VL3L1X, VL53L4CD, VL53L4ED) 

Figure 8  shows the ranging reported by device with two crosstalk levels measured with two different cover glasses for sigma delta sensors. 

Crosstalk is affecting the linearity and the maximum ranging distance. 

The higher the crosstalk signal, the greater the range linearity is affected at short distance. 

Figure 8.  Range vs target distance for high and low levels of crosstalk for sigma delta sensors 

No crosstalk calibration or correction 

After crosstalk calibration, we can recover the ranging linearity and filter the false targets induced by crosstalk. 

The ranging with crosstalk correction is shown on  Figure 9 . All false targets have been removed, and linearity is recovered. 

## AN5231    

> Crosstalk compensation
> AN5231 -Rev 2 page 9/21

Figure 9.  Range vs target distance for high and low levels of crosstalk for sigma delta sensors 

Crosstalk calibration and correction applied 

4.1.2  Histogram sensors (VL53L1CB, VL53L3CX, VL53L4CX) 

For histogram sensors, the crosstalk effect on ranging is slightly different than the sigma delta ones.  Figure 10 

shows the ranging curves for a device with high crosstalk cover glass, when no crosstalk correction is applied. 

Below 80 cm, the signal coming back from the cover glass is creating false targets and ranging errors.  Above 

80 cm, two ranging values are reported by the device: the distance of the target and the distance of the cover 

glass. 

Figure 10.  Range vs target distance for high levels of crosstalk for histogram sensors 

No crosstalk calibration or correction 

After crosstalk calibration and correction, as shown on  Figure 11 , the ranging distance is now OK. False targets and ranging errors have been removed. 

## AN5231    

> Crosstalk compensation
> AN5231 -Rev 2 page 10/21

Figure 11.  Range vs target distance for high levels of crosstalk for histogram sensors 

Crosstalk calibration and correction applied 

## 4.2  Cover glass materials 

A single material is recommended for the cover glass design. This is because multimaterials may alter the performance or increase the internal light scattering effect. 

Material transmission should be greater than 85%. 

• PMMA (94% transmission) and acrylic (92% transmission) 

• Tempered glass or polycarbonate is acceptable (85-88% transmission) 

• PET is not recommended (80% transmission) 

## 4.3  Cover glass coating 

The cover glasses are normally coated with different materials for different purposes. 

• Colored ink for aesthetic reasons 

• IR filter to cut off all the unwanted light in the IR transmission. Normally the filter coating is deposited on the back side of the window. 

• ARC: antireflective coating to reduce the surface reflectance. 

• AFC: antifingerprint coating to increase the fingerprint protection. 

It is important to note that superficial coating may generate additional crosstalk signals. In fact, any transmissive layer deposited on the cover glass could act as an optical path to guide the crosstalk light from the emitter to the receiver. 

Additional coatings are not recommended. The impact of coatings (ARC or AFC) on system performance should be assessed. 

## AN5231    

> Crosstalk compensation
> AN5231 -Rev 2 page 11/21

# 5 Cover glass design 

The module is typically used with a cover glass. It serves two main purposes: 

• It provides physical protection for the module, including dust ingress prevention 

• It provides additional optical filtering for the module. 

The following section describes the two types of cover glass that can be used. 

## 5.1  Two-glass adapter (ideal solution) 

The two-glass adapter is the only topology that allows the sensor to: 

• Eliminate the impact of dust or fingerprints 

• Improve ranging performance 

• Suppress some calibration steps 

Dust typically reduces the maximum working distance depending on its concentration. However, in the first figure below, we can see that the maximum ranging distance remains more than 2 m, thanks to the use of a two-glass cover window. In the second figure below, the target can still be seen despite the fact that both the emitter and receiver are fully covered in dust. 

Figure 12.  Two-glass adapter showing dust immunity 

## AN5231    

> Cover glass design
> AN5231 -Rev 2 page 12/21

Figure 13.  Two-glass adapter still working in dusty conditions: example with the VL53L1X 

Figure 14.  Example of a two-adapter adapter used on an X-NUCLEO-53L1A1 expansion board 

## AN5231 

Cover glass design    

> AN5231 -Rev 2 page 13/21

## 5.2  Plain cover glass 

For aesthetic reasons, a two-glass adapter may not always be possible for ID products. In other cases, some manufacturers using specific cover glass materials (for example, Corning Gorilla Glass) are not able to split the cover glass into two pieces. In both situations above, a plain cover glass may be used. ST recommends that the cover glass be opaque. IR ink can be used over exclusion areas (see  Section 6:  Cover glass mechanical 

guidelines  for more details). 

Note that by using a plain cover glass, the module is subject to crosstalk which is generated by the cover glass itself. ST recommends that crosstalk be minimized so as to achieve the best ranging performance. The 

Section 3:  Crosstalk contributing factors and recommendations to reduce it  describes the main contributors to crosstalk and suggests recommendations for reducing their affect. This section also looks at the impact of crosstalk on ranging performance and how crosstalk can be compensated. 

## 5.3  Gaskets 

Gaskets reduce the crosstalk between the true signal and spurious reflections from the transmitted signal. The ideal gasket should be thick enough to fill the full air gap between the device and the cover glass. The gasket should contain two apertures large enough to allow the full Tx or Rx cone to pass through unimpeded. The gasket should also form a light barrier between the Rx and the Tx channels. In addition, the gasket should cover the maximum area between the Rx and Tx channels possible without impeding the keep out zones. 

Figure 15.  Example of gasket with VL53L1X 

## AN5231    

> Cover glass design
> AN5231 -Rev 2 page 14/21

# 6 Cover glass mechanical guidelines 

For aesthetic reasons, the customer may want to add a coating with different optical properties (color, glass finishing, etc.) to the cover glass. 

Exclusion areas are defined as the areas where there must be no coating. 

Exclusion cones provided in the product datasheet (see figure below) include the ST assembly margins. The customer assembly margins must be considered to define the exclusion area sizes. 

Figure 16.  Example of VL53L1X exclusion cones 

An Microsoft Excel® spreadsheet is available on st.com  here , to calculate the exclusion areas. The following input parameters are considered: 

• Cover glass refractive index (index) 

• Cover glass thickness (Y) 

• Air gap (X) 

• Cover glass tilt (V) 

• Customer manufacturing assembly tolerance: 

– X, Y positioning (Xtol, Ytol) 

– Z-height positioning (Ztol) 

– Assembly pitch tilt (pitch) 

– Assembly roll tilt (roll) 

Two topologies are considered: 

• Oval exclusion area including both emitter and receiver 

• Two-hole exclusion area, one for the emitter and one for the receiver 

## AN5231    

> Cover glass mechanical guidelines
> AN5231 -Rev 2 page 15/21

Figure 17.  Inputs (in yellow) for Excel sheet 

## 6.1  Oval exclusion area 

The figure below shows the design of the oval exclusion area. The exclusion zone is a single oval area covering both the emitter and the receiver. 

Figure 18.  Oval exclusion area 

## AN5231 

Cover glass mechanical guidelines    

> AN5231 -Rev 2 page 16/21

## 6.2  Two-hole exclusion area 

The figure below shows the design of the two-hole exclusion area. The exclusion zone is composed of two holes, one on the emitter and one on the receiver. 

Figure 19.  Two holes exclusion area 

## AN5231 

Cover glass mechanical guidelines    

> AN5231 -Rev 2 page 17/21

# 7 Conclusion and summary tables 

The figure below summarizes the main contributers to crosstalk. 

Table 2.  Some of the major contributors to crosstalk and their consequences 

Contributor  Trend  Impact on Crosstalk 

Cover glass transmission  ↗ ↘

Cover glass scattering/haze  ↗ ↘

Cover glass thickness  ↗ ↗

Cover glass angle  ↗ ↗ up to 25° 

then ↘

Airgap  ↗ ↗

Gasket presence  Yes  ↘

Cover glass recommendation details are reported in the summary table below. 

Table 3.  Parameter recommendations per device 

Figures below are for the final cover glass including any coatings applied. 

Parameter  Recommended specification  VL53L0X  VL53L3CX/ VL53L4CX 

VL53L4CD/ VL53L4ED 

VL53L1CX/ VL53L1CB 

Optical parameter 

Maximum crosstalk signal level accepted 

Most critical parameter of the design. As low as possible. 

<0.7kcps/SPAD  <0.8kcps/SPAD  <1kcps/SPAD  <3kcps/SPAD 

Transmittance at 940 nm 

As high as possible.  >87%  >87%  >87%  >87% 

Transmittance haze (visible light) 

As low as possible.  <2%*  <2%*  <2%*  <2%* 

Transmittance haze (940 nm) 

As low as possible.  <1%*  <1%*  <1%*  <1%* 

Recommended optical material 

Single layer (Gorilla glass, PMMA, polycarbonate) with or without coating. Adding ARC can improve transmittance 

Mechanical parameter 

Airgap (with gasket) 

The lower the airgap, the lower the crosstalk 

<1.0 mm  <1.3 mm  <1.0 mm  <1.0 mm max 

Airgap (no gasket)  <0.6 mm  1.0 mm max  <0.6 mm  <0.4 mm 

Cover glass roll tilt 

As low as possible including assembly tolerances** 

<5°  <5°  <5°  <5° 

Number of cover glass apertures  Two circular holes are preferable*** 

Note:  For a particular turnkey cover glass made by third-party, contact your STMicroelectronics' sales office. 

## AN5231 

Conclusion and summary tables    

> AN5231 -Rev 2 page 18/21

## Revision history 

Table 4.  Document revision history 

Date  Version  Changes 

15-Nov-2018  1 Initial release 

07-May-2026  2

Added root part numbers VL53L3CX, VL53L4CD, VL53L4CX, and VL53L4ED. 

Changed term "cover window" by "cover glass" throughout. 

Removed section  Off-the-shelf solution Hornix cover window .

Removed images:  ST's two glass adapter step file (3D view) , Two glass 

adapter drawing (2D view) , and  Video of two glass adapter solution .

Added  Section 3.5:  Cover glass thickness .

Section 4:  Crosstalk compensation : Modified and added subsections. 

## AN5231    

> AN5231 -Rev 2 page 19/21

# Contents 

## 1 Acronyms and abbreviations  . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . 2

## 2 General information  . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . 3

2.1  System crosstalk  . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . .  4

## 3 Crosstalk contributing factors and recommendations to reduce it  . . . . . . . . . . . . . . . . . .  5

3.1  Optical transmission  . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . .  5

3.2  Haze  . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . .  5

3.3  Smudge  . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . .  6

3.4  Cover glass tilt and surface parallelism  . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . .  6

3.5  Cover glass thickness . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . .  6

3.6  Air gap  . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . .  7

3.7  External contributors  . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . .  8

## 4 Crosstalk compensation  . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . 9

4.1  Crosstalk impact on ranging  . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . .  9      

> 4.1.1 Sigma delta sensors (VL53L0X, VL3L1X, VL53L4CD, VL53L4ED) . . . . . . . . . . . . . . . . . . . 9
> 4.1.2 Histogram sensors (VL53L1CB, VL53L3CX, VL53L4CX) . . . . . . . . . . . . . . . . . . . . . . . . . 10

4.2  Cover glass materials  . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . .  11 

4.3  Cover glass coating  . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . .  11 

## 5 Cover glass design  . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . 12 

5.1  Two-glass adapter (ideal solution)  . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . .  12 

5.2  Plain cover glass  . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . .  14 

5.3  Gaskets  . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . .  14 

## 6 Cover glass mechanical guidelines  . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . 15 

6.1  Oval exclusion area  . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . .  16 

6.2  Two-hole exclusion area . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . .  17 

## 7 Conclusion and summary tables  . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . 18 

## Revision history  . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . 19 

## AN5231    

> Contents
> AN5231 -Rev 2 page 20/21

IMPORTANT NOTICE – READ CAREFULLY 

STMicroelectronics NV and its subsidiaries (“ST”) reserve the right to make changes, corrections, enhancements, modifications, and improvements to ST products and/or to this document at any time without notice. 

In the event of any conflict between the provisions of this document and the provisions of any contractual arrangement in force between the purchasers and ST, the provisions of such contractual arrangement shall prevail. 

The purchasers should obtain the latest relevant information on ST products before placing orders. ST products are sold pursuant to ST’s terms and conditions of sale in place at the time of order acknowledgment. 

The purchasers are solely responsible for the choice, selection, and use of ST products and ST assumes no liability for application assistance or the design of the purchasers’ products. 

No license, express or implied, to any intellectual property right is granted by ST herein. 

Resale of ST products with provisions different from the information set forth herein shall void any warranty granted by ST for such product. 

If the purchasers identify an ST product that meets their functional and performance requirements but that is not designated for the purchasers' market segment, the purchasers shall contact ST for more information. 

ST and the ST logo are trademarks of ST. For additional information about ST trademarks, refer to  www.st.com/trademarks . All other product or service names are the property of their respective owners. 

Information in this document supersedes and replaces information previously supplied in any prior versions of this document. 

© 2026  STMicroelectronics – All rights reserved 

## AN5231 

AN5231 - Rev 2 page 21/21
