//
// COPYRIGHT LICENSE: This information contains sample code provided in source code form. You may copy, 
// modify, and distribute these sample programs in any form without payment to IBM for the purposes of 
// developing, using, marketing or distributing application programs conforming to the application 
// programming interface for the operating platform for which the sample code is written. 
// Notwithstanding anything to the contrary, IBM PROVIDES THE SAMPLE SOURCE CODE ON AN "AS IS" BASIS 
// AND IBM DISCLAIMS ALL WARRANTIES, EXPRESS OR IMPLIED, INCLUDING, BUT NOT LIMITED TO, ANY IMPLIED 
// WARRANTIES OR CONDITIONS OF MERCHANTABILITY, SATISFACTORY QUALITY, FITNESS FOR A PARTICULAR PURPOSE, 
// TITLE, AND ANY WARRANTY OR CONDITION OF NON-INFRINGEMENT. IBM SHALL NOT BE LIABLE FOR ANY DIRECT, 
// INDIRECT, INCIDENTAL, SPECIAL OR CONSEQUENTIAL DAMAGES ARISING OUT OF THE USE OR OPERATION OF THE 
// SAMPLE SOURCE CODE. IBM HAS NO OBLIGATION TO PROVIDE MAINTENANCE, SUPPORT, UPDATES, ENHANCEMENTS 
// OR MODIFICATIONS TO THE SAMPLE SOURCE CODE.  
//
// (C) COPYRIGHT International Business Machines Corp., 2001,2011
// All Rights Reserved * Licensed Materials - Property of IBM
//
package com.ibm.websphere.samples.pbw.jpa;

import java.io.Serializable;

/**
 * The key class of the Order entity bean.
 **/
public class OrderKey implements Serializable {

	private static final long serialVersionUID = 7912030586849592135L;

	public String orderID;

	/**
	 * Constructs an OrderKey object.
	 */
	public OrderKey() {
	}

	/**
	 * Constructs a newly allocated OrderKey object that represents the primitive long argument.
	 */
	public OrderKey(String orderID) {
		this.orderID = orderID;
	}

	/**
	 * Determines if the OrderKey object passed to the method matches this OrderKey object.
	 * 
	 * @param obj
	 *            java.lang.Object The OrderKey object to compare to this OrderKey object.
	 * @return boolean The pass object is either equal to this OrderKey object (true) or not.
	 */
	public boolean equals(Object obj) {
		if (obj instanceof OrderKey) {
			OrderKey otherKey = (OrderKey) obj;
			return (((orderID.equals(otherKey.orderID))));
		} else
			return false;
	}

	/**
	 * Generates a hash code for this OrderKey object.
	 * 
	 * @return int The hash code.
	 */
	public int hashCode() {
		return (orderID.hashCode());
	}

	/**
	 * Get accessor for persistent attribute: orderID
	 */
	public java.lang.String getOrderID() {
		return orderID;
	}

	/**
	 * Set accessor for persistent attribute: orderID
	 */
	public void setOrderID(java.lang.String newOrderID) {
		orderID = newOrderID;
	}

}
