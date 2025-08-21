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
package com.ibm.websphere.samples.daytrader.web.prims.beanval;

import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.List;

import javax.enterprise.context.RequestScoped;
import javax.validation.constraints.Min;
import javax.validation.constraints.NotNull;
import javax.validation.constraints.PastOrPresent;
import javax.validation.constraints.Size;

@RequestScoped
public class CDIMethodConstraintBean {

  private static int hitCount = 0;
  private List<Integer> list = new ArrayList<>();

  // Dumb primitive, beanval checks that the date passed in is valid and that the 
  // return is > 0;
  @Min(1)
  public int getHitCount(@NotNull @PastOrPresent LocalDateTime now) {
    list.add(++hitCount);
    return hitCount;
  }
  
  @Size(max=1)
  public List<@Min(1) Integer> hitList() {
    return list;
  }

}
