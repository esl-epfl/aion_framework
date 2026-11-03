-- ================================================================
--  mac8 -- 8x8 unsigned multiply-accumulate, the framework's VHDL example
--
--    clr = 1        acc <= 0
--    en  = 1        acc <= acc + a * b        (wraps at 2^20)
--    otherwise      acc holds
--
--  Small enough to run through the whole flow in minutes, and an array of
--  full adders is exactly what the miner (step 2) and the functional cell
--  extractor (`make ext-rank`) have something to say about.
-- ================================================================

library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

use work.mac8_pkg.all;

entity mac8 is
  port (
    clk   : in  std_logic;
    rst_n : in  std_logic;
    en    : in  std_logic;
    clr   : in  std_logic;
    a     : in  std_logic_vector(OPERAND_BITS - 1 downto 0);
    b     : in  std_logic_vector(OPERAND_BITS - 1 downto 0);
    acc   : out std_logic_vector(ACC_BITS - 1 downto 0)
  );
end entity;

architecture rtl of mac8 is
  signal acc_q   : unsigned(ACC_BITS - 1 downto 0);
  signal product : unsigned(2 * OPERAND_BITS - 1 downto 0);
begin
  product <= unsigned(a) * unsigned(b);

  process (clk, rst_n)
  begin
    if rst_n = '0' then
      acc_q <= (others => '0');
    elsif rising_edge(clk) then
      if clr = '1' then
        acc_q <= (others => '0');
      elsif en = '1' then
        acc_q <= acc_q + resize(product, ACC_BITS);
      end if;
    end if;
  end process;

  acc <= std_logic_vector(acc_q);
end architecture;
