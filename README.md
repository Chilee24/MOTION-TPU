**Tập hợp và tham số:**
- Tập các điểm:  
  $ I = \{0, 1, 2, \dots, N\} $  
  Trong đó:
  - $ 0 $ là depot (trụ sở).
  - $ 1, 2, \dots, N $ là các khách hàng.
- Tập các nhân viên kỹ thuật:  
  $ K = \{1, 2, \dots, K\} $.
- Tham số:
  - $ d(i) $: thời gian bảo trì tại khách hàng $ i $ (với $ d(0)=0 $).
  - $ t(i,j) $: thời gian di chuyển từ điểm $ i $ đến điểm $ j $.

**Biến quyết định:**
- $ x_{ij}^k \in \{0,1\} $:  
  $ x_{ij}^k = 1 $ nếu nhân viên $ k $ di chuyển trực tiếp từ điểm $ i $ đến điểm $ j $; ngược lại $ x_{ij}^k = 0 $.
- $ u_i^k \ge 0 $:  
  Thời gian mà nhân viên $ k $ đến điểm $ i $ (hoặc bắt đầu bảo trì tại $ i $).
- $ T \ge 0 $:  
  Thời gian làm việc lớn nhất của các nhân viên, mục tiêu cần tối thiểu hóa.

**Hàm mục tiêu:**

$
\min T
$

**Các ràng buộc:**

 **Mỗi khách hàng được thăm đúng một lần:**

   $
   \sum_{k \in K} \sum_{\substack{j \in I \\ j \neq i}} x_{ij}^k = 1,\quad \forall i = 1, \dots, N.
   $

**Xuất phát từ depot cho mỗi nhân viên:**
  Mỗi nhân viên bắt đầu và kết thúc tại depot (trụ sở).

   $
   \sum_{\substack{j \in I \\ j \neq 0}} x_{0j}^k = 1,\quad \forall k \in K.
   $


   $
   \sum_{\substack{i \in I \\ i \neq 0}} x_{i0}^k = 1,\quad \forall k \in K.
   $

**Bảo toàn luồng tại các khách hàng:**   Đảm bảo luồng đi vào và ra tại mỗi khách hàng được cân bằng trong lộ trình của từng nhân viên.
  

   $
   \sum_{\substack{j \in I \\ j \neq i}} x_{ij}^k - \sum_{\substack{j \in I \\ j \neq i}} x_{ji}^k = 0,\quad \forall i = 1, \dots, N,\ \forall k \in K.
   $

**Ràng buộc thời gian :** bảo rằng nếu nhân viên 𝑘  đi từ 𝑖 đến 𝑗 thì thời gian đến 𝑗 phải lớn hơn thời gian đến 𝑖 cộng với thời gian bảo trì tại 𝑖 và thời gian di chuyển từ 𝑖 đến 𝑗.
   
   $
   u_j^k \ge u_i^k + d(i) + t(i,j) - M \left(1 - x_{ij}^k\right),\quad \forall i,j \in I,\ i\neq j,\ \forall k \in K.
   $

**Khởi tạo thời gian tại depot:**

   $
   u_0^k = 0,\quad \forall k \in K.
   $

 **Xác định thời gian hoàn thành lộ trình:**
  Đảm bảo $ T $ lớn hơn hoặc bằng thời gian hoàn thành của lộ trình của mỗi nhân viên.
  
   $
   T \ge u_i^k + d(i) + t(i,0) - M \left(1 - x_{i0}^k\right),\quad \forall i \in I,\ \forall k \in K.
   $


