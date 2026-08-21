Shader "BCI/M7.4/Environment Depth Array Copy"
{
    SubShader
    {
        Tags { "RenderType" = "Opaque" }

        Pass
        {
            Name "Explicit Left Depth View Copy"
            Cull Off
            ZWrite Off
            ZTest Always

            CGPROGRAM
            #pragma target 4.5
            #pragma vertex Vert
            #pragma fragment Frag

            // This declaration and float3(uv, slice) sampling follow AR Foundation 6.5's
            // SoftOcclusionPreprocessing.shader for Meta OpenXR environment depth.
            Texture2DArray_half _EnvironmentDepthTexture;
            SamplerState sampler_EnvironmentDepthTexture;
            float _EnvironmentDepthViewSlice;

            struct Varyings
            {
                float4 positionCS : SV_POSITION;
                float2 uv : TEXCOORD0;
            };

            Varyings Vert(uint vertexId : SV_VertexID)
            {
                Varyings output;
                float2 uv = float2((vertexId << 1) & 2, vertexId & 2);
                output.positionCS = float4(uv * 2.0 - 1.0, 0.0, 1.0);
                // Matches the UV orientation used by AR Foundation's occlusion preprocessor.
                output.uv = float2(uv.x, 1.0 - uv.y);
                return output;
            }

            float4 Frag(Varyings input) : SV_Target
            {
                float rawNormalizedDepth = _EnvironmentDepthTexture.SampleLevel(
                    sampler_EnvironmentDepthTexture,
                    float3(input.uv, _EnvironmentDepthViewSlice),
                    0).r;
                return float4(rawNormalizedDepth, 0.0, 0.0, 1.0);
            }
            ENDCG
        }
    }
}
