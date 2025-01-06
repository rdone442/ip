# IP 地址更新器

自动获取并更新指定域名的IP地址列表。

## 功能

- 每小时自动更新一次IP地址列表
- 自动识别IPv4和IPv6地址
- 使用GeoIP2数据库查询IP所属国家
- 支持多个端口配置
- 自动提交更新到仓库

## 环境变量配置

有两种方式设置环境变量：

1. 使用.env文件（本地开发推荐）：
   创建`.env`文件并添加以下内容：
   ```
   TARGET_DOMAIN=your.domain.com
   TARGET_PORTS=443,80,8443
   ```

2. GitHub Actions（部署时使用）：
   在仓库的Settings -> Secrets and variables -> Actions中添加以下密钥：
   - `TARGET_DOMAIN`: 要查询的域名（必需）
   - `TARGET_PORTS`: 要检查的端口，多个端口用逗号分隔（可选，默认为443）

**注意：** 
- 必须设置 `TARGET_DOMAIN` 环境变量
- `TARGET_PORTS` 支持多个端口，用逗号分隔，如：`443,80,8443`

## 文件说明

- `ip.txt`: 最新的IP地址列表
- `ip.py`: IP地址获取脚本
- `.github/workflows/update-ip.yml`: GitHub Actions自动化配置
- `.env`: 本地环境变量配置（不要提交到Git）

## 输出格式

每行一个IP和端口的组合，格式为： 