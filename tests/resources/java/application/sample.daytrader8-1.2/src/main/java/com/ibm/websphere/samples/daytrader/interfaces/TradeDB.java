/**
 * (C) Copyright IBM Corporation 2019.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */
package com.ibm.websphere.samples.daytrader.interfaces;

import com.ibm.websphere.samples.daytrader.beans.RunStatsDataBean;

public interface TradeDB {
  
  /**
   * Reset the TradeData by - removing all newly registered users by scenario
   * servlet (i.e. users with userID's beginning with "ru:") * - removing all
   * buy/sell order pairs - setting logoutCount = loginCount
   *
   * return statistics for this benchmark run
   */
  RunStatsDataBean resetTrade(boolean deleteAll) throws Exception;

  /**
   * Get the Database Product Name
   *
   * return DB Product Name String
   */
  String checkDBProductName() throws Exception;

  /**
   * Get the impl for the TradeService
   *
   * return int matching the implementation
   */

}
