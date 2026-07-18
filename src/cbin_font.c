#include "cbin_font.h"

#include <stdint.h>
#include <stdlib.h>
#include <string.h>

static void* lv_malloc_copy(const void* source, size_t size) {
    void* destination = lv_malloc(size);
    LV_ASSERT_MALLOC(destination);
    if (destination != NULL) {
        memcpy(destination, source, size);
    }
    return destination;
}

static void add_base(void** address, uintptr_t base) {
    if (*address != NULL) {
        *address = (void*)((uintptr_t)*address + base);
    }
}

lv_image_dsc_t* cbin_img_dsc_create(uint8_t* bin_addr) {
    lv_image_dsc_t* image = lv_malloc_copy(bin_addr, sizeof(lv_image_dsc_t));
    if (image != NULL) {
        add_base((void**)&image->data, (uintptr_t)bin_addr);
    }
    return image;
}

void cbin_img_dsc_delete(lv_image_dsc_t* image) {
    lv_free(image);
}

lv_font_t* cbin_font_create(uint8_t* bin_addr) {
    lv_font_t* font = lv_malloc_copy(bin_addr, sizeof(lv_font_t));
    if (font == NULL) {
        return NULL;
    }
    font->get_glyph_dsc = lv_font_get_glyph_dsc_fmt_txt;
    font->get_glyph_bitmap = lv_font_get_bitmap_fmt_txt;

    bin_addr += (uintptr_t)font->dsc;
    lv_font_fmt_txt_dsc_t* descriptor = lv_malloc_copy(bin_addr, sizeof(lv_font_fmt_txt_dsc_t));
    if (descriptor == NULL) {
        lv_free(font);
        return NULL;
    }
    font->dsc = descriptor;
    add_base((void**)&descriptor->glyph_bitmap, (uintptr_t)bin_addr);
    add_base((void**)&descriptor->glyph_dsc, (uintptr_t)bin_addr);

    if (descriptor->cmap_num != 0) {
        uint8_t* cmaps_addr = bin_addr + (uintptr_t)descriptor->cmaps;
        descriptor->cmaps = lv_malloc(sizeof(lv_font_fmt_txt_cmap_t) * descriptor->cmap_num);
        LV_ASSERT_MALLOC(descriptor->cmaps);
        if (descriptor->cmaps == NULL) {
            lv_free(descriptor);
            lv_free(font);
            return NULL;
        }
        uint8_t* cursor = cmaps_addr;
        for (int index = 0; index < descriptor->cmap_num; ++index) {
            lv_font_fmt_txt_cmap_t* cmap = (lv_font_fmt_txt_cmap_t*)&descriptor->cmaps[index];
            cmap->range_start = *(uint32_t*)cursor;
            cursor += 4;
            cmap->range_length = *(uint16_t*)cursor;
            cursor += 2;
            cmap->glyph_id_start = *(uint16_t*)cursor;
            cursor += 2;
            cmap->unicode_list = (const uint16_t*)(uintptr_t)(*(uint32_t*)cursor);
            cursor += 4;
            cmap->glyph_id_ofs_list = (const void*)(uintptr_t)(*(uint32_t*)cursor);
            cursor += 4;
            cmap->list_length = *(uint16_t*)cursor;
            cursor += 2;
            cmap->type = (lv_font_fmt_txt_cmap_type_t)*(uint8_t*)cursor;
            cursor += 2;
            add_base((void**)&cmap->unicode_list, (uintptr_t)cmaps_addr);
            add_base((void**)&cmap->glyph_id_ofs_list, (uintptr_t)cmaps_addr);
        }
    }

    if (descriptor->kern_dsc != NULL) {
        uint8_t* kern_addr = bin_addr + (uintptr_t)descriptor->kern_dsc;
        if (descriptor->kern_classes == 1) {
            lv_font_fmt_txt_kern_classes_t* classes = lv_malloc_copy(kern_addr, sizeof(*classes));
            descriptor->kern_dsc = classes;
            add_base((void**)&classes->class_pair_values, (uintptr_t)kern_addr);
            add_base((void**)&classes->left_class_mapping, (uintptr_t)kern_addr);
            add_base((void**)&classes->right_class_mapping, (uintptr_t)kern_addr);
        } else {
            lv_font_fmt_txt_kern_pair_t* pairs = lv_malloc_copy(kern_addr, sizeof(*pairs));
            descriptor->kern_dsc = pairs;
            add_base((void**)&pairs->glyph_ids, (uintptr_t)kern_addr);
            add_base((void**)&pairs->values, (uintptr_t)kern_addr);
        }
    }
    return font;
}

void cbin_font_delete(lv_font_t* font) {
    if (font == NULL) {
        return;
    }
    lv_font_fmt_txt_dsc_t* descriptor = (lv_font_fmt_txt_dsc_t*)font->dsc;
    if (descriptor != NULL) {
        lv_free((void*)descriptor->cmaps);
        lv_free((void*)descriptor->kern_dsc);
        lv_free(descriptor);
    }
    lv_free(font);
}

