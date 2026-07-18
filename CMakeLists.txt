file(GLOB FONT_SOURCES "${CMAKE_CURRENT_SOURCE_DIR}/src/*.c")

idf_component_register(
    SRCS ${FONT_SOURCES}
    INCLUDE_DIRS "include"
    PRIV_REQUIRES lvgl
)

target_compile_definitions(${COMPONENT_LIB} PUBLIC LV_LVGL_H_INCLUDE_SIMPLE)
