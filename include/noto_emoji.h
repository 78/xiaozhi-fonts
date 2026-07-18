#ifndef NOTO_EMOJI_H
#define NOTO_EMOJI_H

#include <stddef.h>
#include <string.h>

typedef struct { const char* name; const char* utf8_string; } noto_emoji_symbol_t;

#define NOTO_EMOJI_NEUTRAL "\xf0\x9f\x98\xb6"
#define NOTO_EMOJI_HAPPY "\xf0\x9f\x99\x82"
#define NOTO_EMOJI_LAUGHING "\xf0\x9f\x98\x86"
#define NOTO_EMOJI_FUNNY "\xf0\x9f\x98\x82"
#define NOTO_EMOJI_SAD "\xf0\x9f\x98\x94"
#define NOTO_EMOJI_ANGRY "\xf0\x9f\x98\xa0"
#define NOTO_EMOJI_CRYING "\xf0\x9f\x98\xad"
#define NOTO_EMOJI_LOVING "\xf0\x9f\x98\x8d"
#define NOTO_EMOJI_EMBARRASSED "\xf0\x9f\x98\xb3"
#define NOTO_EMOJI_SURPRISED "\xf0\x9f\x98\xaf"
#define NOTO_EMOJI_SHOCKED "\xf0\x9f\x98\xb1"
#define NOTO_EMOJI_THINKING "\xf0\x9f\xa4\x94"
#define NOTO_EMOJI_WINKING "\xf0\x9f\x98\x89"
#define NOTO_EMOJI_COOL "\xf0\x9f\x98\x8e"
#define NOTO_EMOJI_RELAXED "\xf0\x9f\x98\x8c"
#define NOTO_EMOJI_DELICIOUS "\xf0\x9f\xa4\xa4"
#define NOTO_EMOJI_KISSY "\xf0\x9f\x98\x98"
#define NOTO_EMOJI_CONFIDENT "\xf0\x9f\x98\x8f"
#define NOTO_EMOJI_SLEEPY "\xf0\x9f\x98\xb4"
#define NOTO_EMOJI_SILLY "\xf0\x9f\x98\x9c"
#define NOTO_EMOJI_CONFUSED "\xf0\x9f\x99\x84"

extern const noto_emoji_symbol_t noto_emoji_symbols[];
extern const size_t noto_emoji_symbol_count;

static inline const char* noto_emoji_get_utf8(const char* name) {
    if (name == NULL) return NULL;
    for (size_t i = 0; i < noto_emoji_symbol_count; ++i) {
        if (strcmp(noto_emoji_symbols[i].name, name) == 0) return noto_emoji_symbols[i].utf8_string;
    }
    return NULL;
}

#endif  // NOTO_EMOJI_H
