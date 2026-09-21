import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Cloud Run 用のコンテナで、必要なファイルだけを持つ standalone の形で出力する
  output: "standalone",
};

export default nextConfig;
