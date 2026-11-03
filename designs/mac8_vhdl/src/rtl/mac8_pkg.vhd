-- ================================================================
--  mac8 -- widths shared by the datapath
-- ================================================================

library ieee;
use ieee.std_logic_1164.all;

package mac8_pkg is
  constant OPERAND_BITS : natural := 8;
  constant ACC_BITS     : natural := 20;   -- 2*8 + 4 guard bits: 16 products
end package;
