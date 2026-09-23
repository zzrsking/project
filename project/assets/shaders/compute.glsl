#version 430

layout(local_size_x = 8, local_size_y = 8, local_size_z = 1) in;

layout(rgba8, binding = 0) uniform image2D img_out;

void main() {
    ivec2 pos = ivec2(gl_GlobalInvocationID.xy);
    vec4 color = vec4(0.0, 1.0, 0.0, 1.0);
    imageStore(img_out, pos, color);
}