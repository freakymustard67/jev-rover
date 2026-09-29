Title: Application note - AN5191 - Using the programmable region of interest (ROI) with the VL53L1X

URL Source: https://www.st.com/resource/en/application_note/an5191-using-the-programmable-region-of-interest-roi-with-the-vl53l1x-stmicroelectronics.pdf

Number of Pages: 11

Markdown Content:
Introduction 

The VL53L1X allows a region of interest (ROI) to be selected. This document explains how to reduce this ROI from its nominal 27 ° field of view (FoV) down to 15 °. 

Figure 1. VL53L1X system FoV: receiver cone at 27 ° 

# Using the programmable region of interest (ROI) with the VL53L1X 

# AN5191 

Application note      

> AN5191 -Rev 1 -July 2018 - By John Doe For further information contact your local STMicroelectronics sales office. www.st.com

# 1 Document scope 

This document explains how to configure the ROI of the VL53L1X with a view to reducing the FoV of the sensing area on the receiver SPAD array. This can be achieved using the SW driver application programming interface (API) or the evaluation kit graphical user interface (GUI). In this application note: • "ROI selection" means positioning the reduced sensing area at a selected place on the SPAD array to detect and measure the distance of the specified area of interest of the external scene. • "Reduced FoV" means using a reduced number of SPADs for the ROI sensing area to limit the viewing angle of the sensor device. The selected ROI can be defined by any combination of edge co-ordinates on the X-axis and Y-axis, keeping a minimum size of 4x4. 

Figure 2. VL53L1X ROI selection with reduced sensing area (blue represents the active sensing area) 

This document also provides some recommendations for ROI selection, taking into account the ranging distance performance trade off and use case topology. 

AN5191    

> Document scope
> AN5191 -Rev 1 page 2/11

# 2 Time-of-Flight sensing and ROI principles 

The VL53L1X device is an advanced optical device allowing distances measurements and target detection. The device incorporates the Time-of-Flight principle (ToF) which is a method for measuring the distance between a sensor and an object, based on the time difference between the emission of a VCSEL light signal and its return to the sensor, after being reflected by the object. The VL53L1X embeds: • The emitter, a vertical cavity surface emitting laser (VCSEL) • The receiver, an array of single photon avalanche diodes (SPADs) The photons emitted by the laser reflect on the target and trigger avalanches when coming back to the SPAD. The time between the emission of the photons and the avalanche (ToF) is measured and translated into a target distance in the range result register. 

Figure 3. ToF principle 

Although the VCSEL emitted beam is not perfectly symmetrical, it can be considered as a cone. Such a beam has 97 % of the optical power present in a cone of 27 °. Since no optic is used in the outgoing emission path, it is the native performance of the VCSEL. The glass present on the VCSEL is a simple IR filter to reduce interference on the t0 time measurement. 

AN5191    

> Time-of-Flight sensing and ROI principles
> AN5191 -Rev 1 page 3/11

Figure 4. Emitter cone 

On the receiver side, there is a SPAD array covered by a convergent lens. Consequently, the system can be seen as a camera subsystem (see figure below) with a big granularity due to the limited number of SPADs. 

Figure 5. Lens and SPAD subsystem 

The full diagonal FoV of the device receiver is 27 ° using the 16x16 SPADs. The VL53L1X sensor architecture allows ROIs with a reduced set of SPADs to be selected, thereby reducing the sensing area and allowing the receiver FoV to be reduced. The only condition is that the ROI must have an array of at least 4x4 SPADs. To conclude, the emitter FoV cannot be modified by the VCSEL and the system keeps sending FoVs measuring 27 °. Conversely, the receiver FoV can be reduced and it is possible to measure only a part of the scene of interest. 

AN5191    

> Time-of-Flight sensing and ROI principles
> AN5191 -Rev 1 page 4/11

# 3 Setting the SPAD array 

The SPAD array is composed of 16x16 SPADs. The minimum selectable SPAD number is 4 in each direction which have been checked by firmware (i.e if a customer program selects a 2x8 array, the VL53L1X firmware overwrites the parameters and selects a 4x8 array). 

3.1 Addressing the SPAD array 

The SPAD is addressed through the SW driver API (please refer to UM2356 for additional information). To select a FoV and a ROI, the program calls the VL53L1_SetUserROI() routine as follows: 

VL53L1_RoiConfig_t RoiConfig; RoiConfig.UserRois[0].TopLeftX = 9; RoiConfig.UserRois[0].TopLeftY = 13; RoiConfig.UserRois[0].BotRightX = 14; RoiConfig.UserRois[0].BotRightY = 10; Status = VL53L1_SetUserROI(Dev, &RoiConfig); 

Figure 6. ROI coordinates 

AN5191    

> Setting the SPAD array
> AN5191 -Rev 1 page 5/11

3.2 Calibrating the SPAD array 

The offset is ROI dependent. Hence, one offset calibration per region is required if an accuracy of <5 cm is needed. 

3.3 Scanning multiple ROIs 

The VL53L1_SetUserROI() routine must be called prior to the VL53L1_StartMeasurement() . It is then possible to change the ROI between two measurements using the stop function VL53L1_StopMeasurement() .

AN5191    

> Calibrating the SPAD array
> AN5191 -Rev 1 page 6/11

# 4 Performance and recommendations 

4.1 FoV performance 

The table below gives estimations of some achievable FoV’s.     

> ROI zone size (in SPADs) Diagonal FOV covered by the zone 4x4 15 ° (smallest)
> 8x8 20 °
> 16x16 27 ° (largest, full FoV)

4.2 Measurement performance 

When the number of SPADs are reduced, the number of photons received is also reduced. Consequently, the distribution of crosstalk and the return beam are not uniform. Depending on the location of the ROI, measurement performances can differ. The table below gives the minimum distance and ranging error of an ROI measurement in the center of the SPAD array. For this measurement: • Ambient = dark i.e. no IR light in the 940±30 nm range • Target = full ROI with 88 % reflectance i.e. N9.5 Munsell • Timing budget = 30 ms • Voltage and temperature = nominal i.e. 2.8 V and 23 °C • Selected mode = long distance (see UM2356) 

Table 1. Measurement performance table          

> Parameter 16x16 ROI 8x8 centered 4x4 centered
> Minimum distance 3.60 m 3.1 m 1.7 m Ranging error ±20 mm ±20 mm ±20 mm

4.3 Ranging distance performance trade off 

If the ROI is reduced, the standard deviation of the mean ranging value is increased and the maximum distance is reduced compared to the full area. To improve this i.e to improve repeatability, the measurement time can be increased. For example, the ranging time is usually 30 ms. If the measurement time is increased by two, the standard deviation is divided by the root of two. 

4.4 Topology recommendation 

In some applications, a reduced ROI or a variable FoV is required. For example, in the figure below, because the FoV is emitted at 27 °, part of the light is reflected on the walls which affects the distance measurement accuracy. One part of the full beam still reaches the target and is reflected back to the SPAD area. These are the photons that the ToF sensor should ideally measure. It is then mandatory to limit the reflectivity of the walls and increase the reflectivity of the target. One solution, if possible, is to place a reflector on the target which will increase the number of photons returned directly by the target to the SPAD. 

AN5191    

> Performance and recommendations
> AN5191 -Rev 1 page 7/11

If this is not possible, a lookup table of the result can be registered to improve the accuracy. Due to the mounting tolerance (device on PCB and PCB to equipment), a scan of the full ROI is useful to define what is the best location of the ROI to optimize the number of photons coming back from the target. 

Figure 7. Target inside a tube example 

AN5191    

> Topology recommendation
> AN5191 -Rev 1 page 8/11

Revision history 

Table 2. Document revision history 

Date Version Changes 

30-Jul-2018 1 Initial release 

AN5191    

> AN5191 -Rev 1 page 9/11

# Contents 

1 Document scope . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . .2 2 Time-of-Flight sensing and ROI principles. . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . 3 3 Setting the SPAD array . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . .5 

3.1 Addressing the SPAD array . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . 5 

3.2 Calibrating the SPAD array . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . 5 

3.3 Scanning multiple ROIs . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . 6 

4 Performance and recommendations . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . 7 

4.1 FoV performance. . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . 7 

4.2 Measurement performance . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . 7 

4.3 Ranging distance performance trade off . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . 7 

4.4 Topology recommendation . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . 7 

Revision history . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . .9 AN5191    

> Contents
> AN5191 -Rev 1 page 10/11

IMPORTANT NOTICE – PLEASE READ CAREFULLY 

STMicroelectronics NV and its subsidiaries (“ST”) reserve the right to make changes, corrections, enhancements, modifications, and improvements to ST products and/or to this document at any time without notice. Purchasers should obtain the latest relevant information on ST products before placing orders. ST products are sold pursuant to ST’s terms and conditions of sale in place at the time of order acknowledgement. Purchasers are solely responsible for the choice, selection, and use of ST products and ST assumes no liability for application assistance or the design of Purchasers’ products. No license, express or implied, to any intellectual property right is granted by ST herein. Resale of ST products with provisions different from the information set forth herein shall void any warranty granted by ST for such product. ST and the ST logo are trademarks of ST. All other product or service names are the property of their respective owners. Information in this document supersedes and replaces information previously supplied in any prior versions of this document. © 2018 STMicroelectronics – All rights reserved 

AN5191 

AN5191 - Rev 1 page 11/11
