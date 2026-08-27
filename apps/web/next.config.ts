import type { NextConfig } from "next";

/** 构建可由轻量运行镜像直接启动的 Next.js standalone 产物。 */
const nextConfig: NextConfig = {
  output: "standalone",
};

export default nextConfig;
