import type { NextConfig } from "next";

/** 构建可由轻量运行镜像直接启动的 Next.js standalone 产物。 */
const nextConfig: NextConfig = {
  output: "standalone",
  // 仓库外的 /Users/longyang/package-lock.json 会干扰 Next.js 自动推断项目根目录。
  // 显式限定到当前 Web 应用可减少无关文件监听，并让构建稳定使用本目录的 lockfile。
  turbopack: {
    root: __dirname,
  },
};

export default nextConfig;
