#ifndef GAP_PLOT_AI_DJI_SDK_CONFIG_H
#define GAP_PLOT_AI_DJI_SDK_CONFIG_H

// Keep the Manifold 3 aircraft link identical to the PSDK 3.16.0 sample.
#define DJI_USE_ONLY_UART                (0)
#define DJI_USE_UART_AND_USB_BULK_DEVICE (1)
#define DJI_USE_UART_AND_NETWORK_DEVICE  (2)
#define DJI_USE_ONLY_USB_BULK_DEVICE     (3)
#define DJI_USE_ONLY_NETWORK_DEVICE      (4)

#define CONFIG_HARDWARE_CONNECTION DJI_USE_ONLY_USB_BULK_DEVICE

#endif
