#pragma once

#include <lvgl.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

lv_image_dsc_t* cbin_img_dsc_create(uint8_t* bin_addr);
void cbin_img_dsc_delete(lv_image_dsc_t* image);
lv_font_t* cbin_font_create(uint8_t* bin_addr);
void cbin_font_delete(lv_font_t* font);

#ifdef __cplusplus
}
#endif

